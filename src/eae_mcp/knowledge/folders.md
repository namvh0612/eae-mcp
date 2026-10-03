# Solution Explorer folders

Folders are **logical only** (no directories on disk).

- `General/Folders.xml`: `<Folder Type="<category>" Name=".Logic.Sub"><Items/></Folder>`; categories Basic, Composite, SubApp, CAT, Adapter, DataType, SystemDevice. Each level has its own entry.
- Membership: `<Parent>.Logic.Sub</Parent>` on the type's `Compile` item in `.dfbproj`; a CAT also has `Folder=".Logic"` in its `.cfg`.
- Device folders list members explicitly: `Root` holds `<item>:.Line1</item>`, `.Line1` holds device IDs.
- EAE quirk: renaming a folder in one category rewrites `<Parent>` in all categories; this server only renames within the chosen category.
