# Step 5a: Book Manifest

The book-level `manifest.json` remains the only mutable per-book pointer. Its
rendition entries name version-2 current and retained builds.

The worker enriches rendition entries from `rendition-manifest.json`, exposing
`sections`. Version-1 retained builds are not kept in the published pointer.

On the first version-2 publication for a rendition, the uploader:

1. uploads the complete immutable version-2 build;
2. overwrites the book manifest so only compatible version-2 builds remain;
3. deletes superseded version-1 build prefixes.

Deletion is last and retryable. A deletion failure does not roll back or
invalidate the newly published build.
