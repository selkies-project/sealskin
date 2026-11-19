"""Docker implementation of the provider interface."""

from __future__ import annotations

import asyncio
import io
import logging
import secrets
from collections.abc import Callable
from typing import Any

from docker.errors import APIError, DockerException, ImageNotFound, NotFound
from docker.types import DeviceRequest
from fastapi import HTTPException

from .. import progress, registries
from ..docker_utils import (
    engine_is_podman,
    get_docker_client,
    inspect_self_container,
    prune_dangling_images,
    scan_render_nodes,
    translate_path_to_host,
)
from ..state import state
from .base_provider import (
    INSTANCE_LABEL,
    MANAGED_BY_LABEL,
    SESSION_LABEL,
    BaseProvider,
    epoch,
    instance_labels,
    offered_gpus,
    open_url_command,
)

logger = logging.getLogger(__name__)

ENDED_STATES = ("exited", "dead", "removing")
#: The CDI kind of the NVIDIA GPUs that `nvidia-ctk cdi generate` describes, numbered as the driver numbers them.
CDI_NVIDIA_GPU = "nvidia.com/gpu"


def _bind_volumes(bind_mounts: Any) -> dict[str, dict[str, str]]:
    """Normalise Docker bind mounts (a `volumes` dict or `host:path[:mode]` list) to a dict."""
    if isinstance(bind_mounts, dict):
        return dict(bind_mounts)
    result: dict[str, dict[str, str]] = {}
    for mount in bind_mounts or []:
        host, _, rest = str(mount).partition(":")
        bind, _, mode = rest.partition(":")
        if host and bind:
            result[host] = {"bind": bind, "mode": mode or "rw"}
    return result


class DockerProvider(BaseProvider):
    """Launches applications as Docker containers on the local daemon."""

    bakes = True

    @property
    def client(self) -> Any:
        """The shared Docker client.

        Raises:
            RuntimeError: If the Docker daemon is unreachable.
        """
        return get_docker_client()

    async def inspect_self(self) -> None:
        """Map the server container's mounts, ports, and network (see `inspect_self_container`)."""
        await inspect_self_container()

    async def detect_gpus(self) -> None:
        """Offer the host's render nodes `node_gpus` allows; NVIDIA ones carry their device index."""
        state.available_gpus[:] = offered_gpus(scan_render_nodes())
        logger.info("Detected %d GPU(s): %s", len(state.available_gpus), state.available_gpus)

    async def refresh_gpus(self) -> None:
        """Keep the render nodes found at startup."""

    async def get_local_image_info(self, image_name: str) -> dict[str, Any] | None:
        """Return id and digests of a locally available image.

        Args:
            image_name: Image reference.

        Returns:
            `{"id", "short_id", "digests"}` or `None` when not present.
        """
        try:
            image = await asyncio.to_thread(self.client.images.get, registries.mirror(image_name))
            return {
                "id": image.id,
                "short_id": image.short_id.split(":")[-1],
                "digests": image.attrs.get("RepoDigests", []),
            }
        except ImageNotFound:
            return None
        except APIError as exc:
            logger.error("Docker API error getting local image info for '%s': %s", image_name, exc)
            return None

    async def get_remote_image_digest(self, image_name: str) -> str | None:
        """Return the digest the registry currently serves for an image.

        Args:
            image_name: Image reference.

        Returns:
            The registry digest, or `None` if it could not be determined.
        """
        reference = registries.mirror(image_name)
        try:
            distribution_info = await asyncio.to_thread(
                self.client.api.inspect_distribution, reference, auth_config=registries.docker_auth(reference)
            )
            return distribution_info["Descriptor"]["digest"]
        except APIError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                logger.warning("Image '%s' not found in remote registry.", image_name)
            else:
                logger.error("Docker API error inspecting remote image '%s': %s", image_name, exc)
            return None
        except DockerException as exc:
            logger.error("Docker error inspecting remote image '%s': %s", image_name, exc)
            return None

    async def pull_image(self, image_name: str) -> Any:
        """Pull an image from its registry, or its mirror, with the node's credential for it.

        Args:
            image_name: Image reference.

        Returns:
            The pulled image object.

        Raises:
            APIError: If the pull fails.
        """
        reference = registries.mirror(image_name)
        try:
            logger.info("Pulling latest image for '%s'...", reference)
            image = await asyncio.to_thread(
                self.client.images.pull, reference, auth_config=registries.docker_auth(reference)
            )
            logger.info("Successfully pulled '%s'.", reference)
            return image
        except APIError as exc:
            logger.error("Failed to pull image '%s': %s", image_name, exc)
            raise

    async def prune_images(self) -> None:
        """Remove dangling images left behind by pulls."""
        await prune_dangling_images()

    async def export_web_client(self, image: str, path: str) -> bytes:
        """Copy `path` out of a container of the image that never starts (see `BaseProvider`).

        The archive comes through Docker's copy API, as `docker cp` takes
        it: a container's logs are not binary-safe, so nothing is run.
        """

        def export() -> bytes:
            # Labelled as an instance of this server, so a leaked one goes with the orphans at reconcile.
            container = self.client.containers.create(
                registries.mirror(image),
                entrypoint=["true"],
                network_disabled=True,
                labels={MANAGED_BY_LABEL: "sealskin", INSTANCE_LABEL: state.instance_name, "app.kubernetes.io/component": "web-export"},
            )
            try:
                chunks, _stat = container.get_archive(path)
                return b"".join(chunks)
            finally:
                try:
                    container.remove(force=True)
                except (APIError, DockerException) as exc:
                    logger.warning("Could not remove the export container of '%s': %s", image, exc)

        try:
            return await asyncio.to_thread(export)
        except NotFound as exc:
            raise RuntimeError(f"'{path}' is not in image '{image}': {exc.explanation or exc}") from exc
        except (APIError, DockerException) as exc:
            raise RuntimeError(f"Docker could not export '{path}' from '{image}': {exc}") from exc

    async def build_image(
        self,
        context: bytes,
        tag: str,
        buildargs: dict[str, str],
        labels: dict[str, str],
        log: Callable[[str], None],
        fresh: bool = False,
        idle: int = 900,
    ) -> str:
        """Build an image with the daemon's classic builder (see `BaseProvider`).

        The builder takes the Engine API's build request as it is, so the
        Dockerfile is held to what that builder reads: no BuildKit mounts,
        here-documents, or `COPY --chmod`.
        """

        def build() -> str:
            image_id = ""
            last = ""
            stream = self.client.api.build(
                fileobj=io.BytesIO(context),
                custom_context=True,
                tag=tag,
                buildargs=buildargs,
                labels=labels,
                rm=True,
                forcerm=True,
                pull=False,
                nocache=fresh,
                decode=True,
                timeout=idle,
            )
            for chunk in stream:
                if chunk.get("error"):
                    raise RuntimeError(str(chunk["error"]).strip())
                if chunk.get("aux"):
                    image_id = str(chunk["aux"].get("ID") or image_id)
                text = chunk.get("stream")
                if text is None and chunk.get("status") and "progress" not in chunk:
                    # A pull of another stage's image: one line per state of a layer.
                    text = " ".join(filter(None, [str(chunk.get("id") or ""), str(chunk["status"])]))
                    if text == last:
                        continue
                    last = text
                for line in (text or "").splitlines():
                    if line.strip():
                        log(line.rstrip())
            return image_id

        try:
            return await asyncio.to_thread(build)
        except (APIError, DockerException) as exc:
            raise RuntimeError(f"Docker could not build the image: {getattr(exc, 'explanation', None) or exc}") from exc
        except OSError as exc:
            raise RuntimeError(f"The build stopped: {exc}") from exc

    async def built_images(self, labels: dict[str, str]) -> list[str]:
        """Return the tags of the local images carrying `labels`."""
        images = await asyncio.to_thread(
            self.client.images.list, filters={"label": [f"{key}={value}" for key, value in labels.items()]}
        )
        return [tag for image in images for tag in image.tags]

    async def remove_image(self, reference: str) -> bool:
        """Untag an image, which removes it once no other tag or container holds it."""
        try:
            await asyncio.to_thread(self.client.images.remove, reference)
        except ImageNotFound:
            return True
        except APIError as exc:
            logger.info("The image %s stays: %s", reference, exc.explanation or exc)
            return False
        return True

    async def run_once(self, image: str, script: str, timeout: int) -> tuple[int, str]:
        """Run a script with `/bin/sh` in a container of the image (see `BaseProvider`)."""

        def run() -> tuple[int, str]:
            container = self.client.containers.run(
                registries.mirror(image),
                entrypoint=["/bin/sh", "-c"],
                command=[script],
                detach=True,
                network=state.discovered_network,
                labels={MANAGED_BY_LABEL: "sealskin-bakery"},
            )
            try:
                code = int(container.wait(timeout=timeout).get("StatusCode", 1))
                return code, container.logs(stdout=True, stderr=False).decode("utf-8", "replace")
            finally:
                try:
                    container.remove(force=True)
                except (APIError, DockerException) as exc:
                    logger.warning("Could not remove the throwaway container of '%s': %s", image, exc)

        try:
            return await asyncio.to_thread(run)
        except (APIError, DockerException, OSError) as exc:
            raise RuntimeError(f"Docker could not run '{image}': {exc}") from exc

    async def launch(
        self,
        session_id: str,
        env_vars: dict[str, str],
        volumes: dict[str, Any] | None = None,
        gpu_config: dict[str, Any] | None = None,
        network: str | None = None,
        is_collaboration: bool = False,
        master_token: str | None = None,
        initial_tokens: dict[str, Any] | None = None,
        vpn: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the application container and wait until it answers HTTP.

        See `BaseProvider.launch` for the arguments; volume keys are server
        paths and are translated to host paths here. The container is named
        `sealskin-<session>-<suffix>`: the first eight characters of the
        session id, as a Kubernetes session pod has them, and a random suffix,
        since a session runs one container per application. With `vpn`, the
        container resolves names at the gateway and, once started, has its
        default route pointed at it by the helper (`_route_through`).

        Raises:
            HTTPException: On Docker errors or readiness timeout.
        """
        config = self.app_config["provider_config"]
        image = registries.mirror(config["image"])

        try:
            await asyncio.to_thread(self.client.images.get, image)
        except ImageNotFound:
            logger.info("[%s] Image '%s' not found locally, pulling...", session_id, image)
            progress.step("image", image=image)
            await self.pull_image(image)

        overrides = dict(config.get("docker_overrides") or {})
        bind_mounts = _bind_volumes(overrides.pop("volumes", None))
        run_kwargs: dict[str, Any] = {
            "image": image,
            "name": f"sealskin-{session_id[:8]}-{secrets.token_hex(3)}",
            "detach": True,
            "shm_size": "1g",
            "environment": env_vars,
            "devices": [],
            "remove": True,
            "network": network,
        }
        run_kwargs.update(overrides)
        run_kwargs["devices"] = list(run_kwargs["devices"])
        run_kwargs["labels"] = {**(run_kwargs.get("labels") or {}), **instance_labels(session_id)}
        run_kwargs["volumes"] = {
            translate_path_to_host(path): bind for path, bind in (volumes or {}).items()
        }
        run_kwargs["volumes"].update(bind_mounts)
        if vpn:
            run_kwargs["volumes"][vpn["resolv_conf"]] = {"bind": "/etc/resolv.conf", "mode": "ro"}

        if gpu_config:
            # The engine is asked once, and off the event loop.
            podman = gpu_config["type"] == "nvidia" and await asyncio.to_thread(engine_is_podman)
            if podman:
                # Podman takes the runtime and an index request without a word and gives no GPU; its GPUs are CDI
                # devices, whose spec brings `/dev/nvidia-modeset` along.
                run_kwargs["device_requests"] = [
                    DeviceRequest(driver="cdi", device_ids=[f"{CDI_NVIDIA_GPU}={gpu_config['index']}"])
                ]
                logger.info(
                    "[%s] Configuring container with Nvidia GPU index %s through CDI",
                    session_id,
                    gpu_config["index"],
                )
            elif gpu_config["type"] == "nvidia":
                run_kwargs["runtime"] = "nvidia"
                run_kwargs["device_requests"] = [
                    DeviceRequest(
                        device_ids=[str(gpu_config["index"])],
                        capabilities=[
                            ["compute", "video", "graphics", "utility", "gpu", "display"]
                        ],
                    )
                ]
                run_kwargs["devices"].append("/dev/nvidia-modeset:/dev/nvidia-modeset")
                logger.info(
                    "[%s] Configuring container with Nvidia GPU index %s",
                    session_id,
                    gpu_config["index"],
                )
            elif gpu_config["type"] == "dri3":
                device_path = gpu_config["device"]
                run_kwargs["devices"].append(f"{device_path}:{device_path}")
                logger.info(
                    "[%s] Configuring container with DRI3 device %s", session_id, device_path
                )

        try:
            try:
                progress.step("starting")
                container = await asyncio.to_thread(self.client.containers.run, **run_kwargs)
            except APIError as exc:
                error_msg = str(exc).lower()
                if "/dev/nvidia-modeset" in error_msg and "no such file or directory" in error_msg:
                    run_kwargs["devices"].remove("/dev/nvidia-modeset:/dev/nvidia-modeset")
                    container = await asyncio.to_thread(self.client.containers.run, **run_kwargs)
                else:
                    raise
            logger.info(
                "[%s] Launched container %s from image %s.", session_id, container.short_id, image
            )
        except ImageNotFound as exc:
            logger.error("[%s] Image '%s' not found after pull attempt.", session_id, image)
            raise HTTPException(
                status_code=500, detail=f"Application image '{image}' not found on host."
            ) from exc
        except APIError as exc:
            logger.error("[%s] Docker API error on launch: %s", session_id, exc)
            await self._remove_unstarted(run_kwargs["name"], session_id)
            if "unresolvable cdi devices" in str(exc).lower():
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "No CDI spec on this host names the NVIDIA GPU. Generate one with "
                        "`nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml`."
                    ),
                ) from exc
            if "could not select device driver" in str(exc) or "nvidia-container-runtime" in str(
                exc
            ):
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Nvidia runtime error on host. Is nvidia-container-toolkit installed "
                        "and configured?"
                    ),
                ) from exc
            raise HTTPException(
                status_code=500, detail=f"Docker error: {exc.explanation}"
            ) from exc

        if vpn:
            try:
                await self._route_through(session_id, container.id, vpn["gateway_ip"])
            except Exception as exc:
                await self.stop(container.id)
                logger.error("[%s] Could not route the container through the VPN gateway: %s", session_id, exc)
                raise HTTPException(status_code=500, detail="The session could not be routed through the VPN gateway.") from exc

        async def current_ip() -> str | None:
            await asyncio.to_thread(container.reload)
            return self._get_container_ip(container.attrs)

        try:
            ip_address = await self._wait_until_ready(
                session_id,
                current_ip,
                env_vars,
                is_collaboration=is_collaboration,
                master_token=master_token,
                initial_tokens=initial_tokens,
            )
        except HTTPException:
            await self.stop(container.id)
            raise
        return {"instance_id": container.id, "ip": ip_address, "port": config["port"]}

    async def _remove_unstarted(self, name: str, session_id: str) -> None:
        """Remove the container a launch of `session_id` created under `name` and could not start.

        Docker removes such a container itself, as `remove` asks it to, while
        Podman leaves it `created`. Only a container that never started and
        carries the session's label goes: a launch can also fail on a name
        that `docker_overrides` fixed and another session's running container
        holds, and the engine's error does not tell the two apart.
        """
        try:
            container = await asyncio.to_thread(self.client.containers.get, name)
            if container.status != "created" or (container.labels or {}).get(SESSION_LABEL) != session_id:
                return
            await asyncio.to_thread(container.remove)
        except NotFound:
            return
        except (APIError, DockerException) as exc:
            logger.warning("Could not remove the unstarted container '%s': %s", name, exc)

    async def _route_through(self, session_id: str, container_id: str, gateway_ip: str) -> None:
        """Point a container's default route at its VPN gateway.

        The helper is the gateway image run in the container's network
        namespace with `NET_ADMIN`, which the container itself never gets;
        the route it sets outlives it.
        """
        from ..settings import settings

        await asyncio.to_thread(
            self.client.containers.run,
            registries.mirror(settings.vpn_image),
            ["route", gateway_ip],
            network_mode=f"container:{container_id}",
            cap_add=["NET_ADMIN"],
            remove=True,
            labels={MANAGED_BY_LABEL: "sealskin-vpn-helper"},
        )
        logger.info("[%s] Routed the container through the VPN gateway at %s.", session_id, gateway_ip)

    async def stop(self, instance_id: str) -> None:
        """Stop (and remove) a container.

        Args:
            instance_id: Container id.
        """
        try:
            container = await asyncio.to_thread(self.client.containers.get, instance_id)
            if container.status == "running":
                logger.info("Stopping container %s...", container.short_id)
                await asyncio.to_thread(container.stop, timeout=5)
                logger.info("Stopped container %s.", container.short_id)
            else:
                logger.info("Container %s is not running, removing it.", container.short_id)
                try:
                    await asyncio.to_thread(container.remove)
                except APIError as exc:
                    if exc.response is None or exc.response.status_code != 409:
                        raise
        except NotFound:
            logger.warning("Attempted to stop container %s, but it was not found.", instance_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Error stopping container %s: %s", instance_id, exc)

    async def is_running(self, instance_id: str) -> bool:
        """Tell whether a container still exists and has not exited."""
        try:
            container = await asyncio.to_thread(self.client.containers.get, instance_id)
        except NotFound:
            return False
        return container.status not in ENDED_STATES

    async def managed_instances(self) -> dict[str, float]:
        """Return this server's labelled containers with their creation times."""
        containers = await asyncio.to_thread(
            self.client.containers.list,
            all=True,
            filters={"label": [f"{MANAGED_BY_LABEL}=sealskin", f"{INSTANCE_LABEL}={state.instance_name}"]},
        )
        return {container.id: epoch(container.attrs["Created"]) for container in containers}

    async def open_url(self, instance_id: str, url: str) -> bool:
        """Run `OPEN_URL_SCRIPT` in a session's container."""
        try:
            container = await asyncio.to_thread(self.client.containers.get, instance_id)
            code, _ = await asyncio.to_thread(container.exec_run, open_url_command(url))
        except (NotFound, APIError) as exc:
            logger.warning("Could not hand %s an address: %s", instance_id[:12], exc)
            return False
        return code == 0

    @staticmethod
    def _get_container_ip(container_attrs: dict[str, Any]) -> str | None:
        """Extract the first usable IP address from container attributes."""
        networks = container_attrs.get("NetworkSettings", {}).get("Networks", {})
        if not networks:
            return None
        if "bridge" in networks:
            return networks["bridge"].get("IPAddress")
        return next(
            (net.get("IPAddress") for net in networks.values() if net.get("IPAddress")), None
        )
