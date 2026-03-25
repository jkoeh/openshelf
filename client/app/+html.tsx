import { ScrollViewStyleReset } from "expo-router/html";

export default function Root({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta httpEquiv="X-UA-Compatible" content="IE=edge" />
        <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no" />
        <meta name="description" content="Open source public domain audiobooks with word-level text/audio sync" />
        <title>OpenShelf</title>

        <ScrollViewStyleReset />

        <style dangerouslySetInnerHTML={{ __html: `body{overflow:hidden;height:100vh}#root{display:flex;height:100vh}` }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
