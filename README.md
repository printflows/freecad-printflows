# PrintFlows for FreeCAD

Send a design from FreeCAD to [printflows.org](https://printflows.org) and get prices from 3D printing makers near you.

The add-on is a small workbench called **PrintFlows** with one command, **Print with PrintFlows** (in French, *Imprimer avec PrintFlows*). It meshes the parts you pick, uploads the mesh and opens the PrintFlows page in your browser, where you choose a maker. Nothing is printed or paid for inside FreeCAD.

Needs FreeCAD 1.0 or newer.

## Install

- **Addon Manager:** not listed yet.
- **By hand:** no release is published yet. Releases will be on the [releases page](https://github.com/printflows/freecad-printflows/releases). Download `PrintFlows-<version>.zip` from there and unzip it into your FreeCAD user `Mod` folder, so you get `Mod/PrintFlows/package.xml`. To find that folder, run `FreeCAD.getUserAppDataDir()` in FreeCAD's Python console and add `Mod`. Restart FreeCAD.

## Use it

1. Switch to the **PrintFlows** workbench.
2. Select the parts to send in the 3D view or the tree (or select nothing and pick "All visible solids").
3. Click **Print with PrintFlows**. The dialog shows:
   - what to send: the selected objects (default) or all visible solids;
   - format: 3MF (default) or binary STL;
   - mesh quality: Low, Medium (default) or High;
   - the overall size in millimetres and the number of parts.
4. Click **Send**. If the mesh has more than 1.5 million triangles you're asked first: PrintFlows can't show a 3D preview above that, though makers can still price it.
5. A progress window shows the upload, with a Cancel button. When it finishes, your browser opens the PrintFlows page. Cancel means no page opens.

Objects without a solid shape (sketches, wires, open surfaces) are left out and named in the dialog, and loose faces inside a compound are left out of it. A selected `App::Part` or group sends the visible solids inside it, in place. A PartDesign Body counts once, not once per feature. A Link is sent as its own part, at the link's place, and the object it points at is still sent when it's visible.

"The selected objects" means exactly what you selected, hidden or not: a hidden object you select in the tree is sent. "All visible solids" skips hidden ones.

In a 3MF file each part keeps its FreeCAD label as the object's name, so a slicer shows the same names.

FreeCAD works in millimetres, and the file is written in millimetres, so nothing is scaled.

## Privacy and the connection

The add-on doesn't connect to anything until you allow it. The first time you click Send it asks, and says what goes where:

- **What:** a mesh copy (3MF or STL) of the parts you chose, the file name, its size, the add-on version and your language.
- **Where:** printflows.org, then the storage link printflows.org hands back. Nothing else, and no telemetry.
- **How long:** PrintFlows keeps the file for 24 hours so the page can load it, then deletes it. No maker sees it until you send a request.

The answer is stored for that server only, in FreeCAD's parameters under `Preferences/Mod/PrintFlows/Consent`. A yes given while testing against another server never covers printflows.org. To withdraw it, choose **PrintFlows > Stop PrintFlows connecting** (*Empêcher PrintFlows de se connecter*): the add-on forgets every yes and asks again before the next send. If what the add-on sends ever changes, everyone is asked again.

If FreeCAD closes during an upload, the temporary copy of the mesh stays in your temp folder. The add-on removes such `PrintFlows-*` folders once they're a day old, the next time you switch to the workbench.

Privacy policy: <https://printflows.org/en/legal/privacy> (French: <https://printflows.org/fr/legal/confidentialite>).

## Language

English, or French when FreeCAD's interface language is French (*Preferences > General > Language*). When FreeCAD follows the system language, the system locale decides.

## For developers

Run these from the repository's root, with Python 3.11 or newer:

```
python build.py            # writes the icon, builds dist/PrintFlows-<version>.zip
python build.py --check    # fails if the client copy differs from its pinned sha256, the icon is stale, or config.VERSION differs from package.xml
python -m unittest discover -s tests -p "test_logic.py"
python tests/run_all.py --freecad <FreeCAD>/bin --user tests/_out/user
```

`run_all.py` copies the add-on into `tests/_out/user/data/Mod`, starts the local mock server (`tools/mock_server.py`), and runs FreeCAD's console and a hidden, offscreen FreeCAD GUI with `FREECAD_USER_HOME`, `FREECAD_USER_DATA` and `FREECAD_USER_TEMP` pointing into that folder. It never contacts printflows.org. Any scratch folder works for `--user`; `tests/_out/` is the one git ignores. On FreeCAD 1.0.x the GUI part is skipped, because that FreeCAD can't run offscreen; the runner says so.

`freecad/PrintFlows/printflows_handoff.py` is PrintFlows' own handoff client (MIT, standard library only). It isn't a third-party package. It's maintained in the PrintFlows CAD repo, together with the Fusion and Blender add-ons, and `build.py` pins it by sha256, so don't edit it here. When the add-on sits in that repo (as its `freecad-addon/` folder, where these commands run from too), `build.py` copies the client in from `../shared`, `--check` also compares it with that copy, and `run_all.py` runs the client's own tests from `../shared/tests` as well. The mock server is then `../tools/mock_server.py`. In this repo the client's tests are skipped, and the runner says so.

### Testing against the mock server

Start `python tools/mock_server.py`; its first line is the base URL. Set `PRINTFLOWS_BASE_URL` to that address (for example `http://127.0.0.1:53817`) before starting FreeCAD. The dialog then says in red that it's in test mode and where the design goes. Only `https` addresses, or `http` on this computer, are accepted. Remove the variable when you're done.

## Licence

MIT, see [LICENSE](LICENSE). The PrintFlows name and logo aren't covered by it. PrintFlows is not affiliated with the FreeCAD project.
