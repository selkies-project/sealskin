/**
 * Generates the mobile shell's iOS, Android, and PWA assets from `assets/logo.png`.
 *
 * One sharp call per output, matching what the shells expect: a flattened
 * 1024px iOS app icon, the universal splash at 2732x2732 in a light and a dark
 * variant across the three scale slots the asset catalog names, the PWA
 * icon ladder, and, when `android/` has been generated, the launcher icons
 * (adaptive and legacy, square and round, per density) and the splash
 * drawables in place of Capacitor's placeholders, so a sideloaded APK shows the
 * same icon as the store build. The logo sits at a fifth of the splash's width,
 * centered.
 */
import sharp from 'sharp';
import { mkdir, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join } from 'node:path';

const LOGO = 'assets/logo.png';
const ICON_BG = '#12161d';
const SPLASH_BG = '#12161d';
const SPLASH_BG_DARK = '#111111';
const SPLASH_PX = 2732;
const LOGO_SCALE = 0.2;
const ICONSET = 'ios/App/App/Assets.xcassets/AppIcon.appiconset';
const SPLASHSET = 'ios/App/App/Assets.xcassets/Splash.imageset';
const PWA_ICONS = [48, 72, 96, 128, 192, 256, 512];
const ANDROID_RES = 'android/app/src/main/res';
// Density buckets with their scale, the legacy launcher icon being 48dp and the adaptive foreground 108dp.
const ANDROID_DENSITIES = { mdpi: 1, hdpi: 1.5, xhdpi: 2, xxhdpi: 3, xxxhdpi: 4 };
const ANDROID_LAUNCHER_DP = 48;
const ANDROID_FOREGROUND_DP = 108;
// The adaptive icon's safe zone is a 66dp circle: the logo fills that much of the foreground layer.
const ANDROID_FOREGROUND_SCALE = 0.6;
const ANDROID_SPLASH = { mdpi: [320, 480], hdpi: [480, 800], xhdpi: [720, 1280], xxhdpi: [960, 1600], xxxhdpi: [1280, 1920] };

const splashName = (scale, dark) => `Default@${scale}x~universal~anyany${dark ? '-dark' : ''}.png`;

async function main() {
  const { width } = await sharp(LOGO).metadata();
  const logoPx = Math.floor(width * LOGO_SCALE);

  await mkdir(ICONSET, { recursive: true });
  await sharp(LOGO).resize(1024, 1024).png().flatten({ background: ICON_BG })
    .toFile(join(ICONSET, 'AppIcon-512@2x.png'));

  await mkdir(SPLASHSET, { recursive: true });
  const logo = await sharp(LOGO).resize(logoPx).toBuffer();
  for (const dark of [false, true]) {
    for (const scale of [1, 2, 3]) {
      await sharp({ create: { width: SPLASH_PX, height: SPLASH_PX, channels: 4,
                              background: dark ? SPLASH_BG_DARK : SPLASH_BG } })
        .composite([{ input: logo, gravity: sharp.gravity.center }])
        .png()
        .toFile(join(SPLASHSET, splashName(scale, dark)));
    }
  }

  await mkdir('icons', { recursive: true });
  for (const px of PWA_ICONS) {
    await sharp(LOGO).resize(px, px).png().toFile(join('icons', `icon-${px}.webp`));
  }
  const android = await androidAssets();
  console.log(`assets: 7 iOS, ${PWA_ICONS.length} PWA, ${android} Android from ${LOGO} (logo ${logoPx}px on ${SPLASH_PX}px)`);
}

/** A sharp pipeline of the logo buffer centered on a field of `background`. */
function onField(width, height, background) {
  return async (logoBuffer) => sharp({ create: { width, height, channels: 4, background } })
    .composite([{ input: logoBuffer, gravity: sharp.gravity.center }]).png();
}

async function logoAt(px) {
  return sharp(LOGO).resize(px, px).toBuffer();
}

/**
 * Write the Android launcher icons and splash drawables into the generated
 * project, when there is one. Returns how many files were written.
 */
async function androidAssets() {
  if (!existsSync(ANDROID_RES)) return 0;
  let written = 0;
  const circle = (px) => Buffer.from(`<svg width="${px}" height="${px}"><circle cx="${px / 2}" cy="${px / 2}" r="${px / 2}"/></svg>`);
  for (const [density, scale] of Object.entries(ANDROID_DENSITIES)) {
    const dir = join(ANDROID_RES, `mipmap-${density}`);
    await mkdir(dir, { recursive: true });
    const launcherPx = ANDROID_LAUNCHER_DP * scale;
    const launcher = await (await onField(launcherPx, launcherPx, ICON_BG)(await logoAt(launcherPx))).toBuffer();
    await sharp(launcher).toFile(join(dir, 'ic_launcher.png'));
    await sharp(launcher).composite([{ input: circle(launcherPx), blend: 'dest-in' }]).png().toFile(join(dir, 'ic_launcher_round.png'));
    const foregroundPx = ANDROID_FOREGROUND_DP * scale;
    await (await onField(foregroundPx, foregroundPx, { r: 0, g: 0, b: 0, alpha: 0 })(await logoAt(Math.round(foregroundPx * ANDROID_FOREGROUND_SCALE))))
      .toFile(join(dir, 'ic_launcher_foreground.png'));
    const [w, h] = ANDROID_SPLASH[density];
    const splashLogo = await logoAt(Math.round(Math.min(w, h) * LOGO_SCALE));
    for (const [orientation, width, height] of [['port', w, h], ['land', h, w]]) {
      const splashDir = join(ANDROID_RES, `drawable-${orientation}-${density}`);
      await mkdir(splashDir, { recursive: true });
      await (await onField(width, height, SPLASH_BG)(splashLogo)).toFile(join(splashDir, 'splash.png'));
    }
    written += 5;
  }
  const [w, h] = ANDROID_SPLASH.mdpi;
  await (await onField(h, w, SPLASH_BG)(await logoAt(Math.round(w * LOGO_SCALE)))).toFile(join(ANDROID_RES, 'drawable', 'splash.png'));
  await writeFile(join(ANDROID_RES, 'values', 'ic_launcher_background.xml'),
    `<?xml version="1.0" encoding="utf-8"?>\n<resources>\n    <color name="ic_launcher_background">${ICON_BG}</color>\n</resources>\n`);
  return written + 2;
}

main();
