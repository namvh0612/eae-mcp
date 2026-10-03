# eHMI (web HMI)

Project `WEB/WEB.htmlproj`; JSON layout + TypeScript logic, rendered in a browser.

| Document | Files | Notes |
|---|---|---|
| Canvas | `WEB/<deviceId>/<Name>.cnv.json`, `.cnv.ts`, `.user.cs` | **Canvases belong to a device** |
| Resolution / navigation | `WEB/<deviceId>/WebCanvasesResolutionList.xml` | Create a resolution before canvases |
| CAT symbol | `WEB/<Cat>/<Cat>_<sym>.sym.json`, `.sym.ts`, `.sym.xml`, `.user.cs`; `<WebSymbol>` in the CAT `.cfg` | class `WEB.Main.Symbols.<Cat>.<sym>` extends `RuntimeSymbol` |
| Graphic | `WEB/<Name>.sym.json/.sym.ts` | reusable, `WEB.Main.Graphics.*` |
| Support class | `WEB/*.spt.ts`, listed in `WebGraphicsList.xml` | TS helpers |

JSON objects: `{"type": "...", "name": "...", "left", "top", "width", "height", …}`. Bindings: on a canvas `"tagName": "<layer FB ID>"`; inside a symbol `"tagName": "<HMI var or sub-CAT>"`, e.g. `System.WEB.Symbols.Base.Label` with `"text": "${Value}"`.
