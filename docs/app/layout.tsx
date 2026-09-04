import { RootProvider } from 'fumadocs-ui/provider/next';
import type { Metadata } from 'next';
import { siteDescription, siteName, siteUrl, withBasePath } from '@/lib/shared';
import './global.css';

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: { template: `%s | ${siteName}`, default: siteName },
  description: siteDescription,
};

export default function Layout({ children }: LayoutProps<'/'>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="flex flex-col min-h-screen">
        <RootProvider
          search={{
            // A static export has no search server, so the index ships to the
            // browser and is queried there.
            options: { type: 'static', api: withBasePath('/api/search') },
          }}
        >
          {children}
        </RootProvider>
      </body>
    </html>
  );
}
