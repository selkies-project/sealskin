/**
 * Generates the mobile shell's iOS and PWA assets from `assets/logo.png`.
 *
 * One sharp call per output, matching what the shells expect: a flattened
 * 1024px iOS app icon, the universal splash at 2732x2732 in a light and a dark
 * variant across the three scale slots the asset catalog names, and the PWA
 * icon ladder. The logo sits at a fifth of the splash's width, centered.
 */
import sharp from 'sharp';
import { mkdir } from 'node:fs/promises';
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
  console.log(`assets: 7 iOS, ${PWA_ICONS.length} PWA from ${LOGO} (logo ${logoPx}px on ${SPLASH_PX}px)`);
}

main();
