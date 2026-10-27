# BAKE ped suite

The JMPPED authoring toolkit (archive bake, character import, preview and
tests) lives here. Its runtime consumer is the compiled-in pedhacks module;
`JMPPED.LEV` is copied into the runtime by `JERICHO/build.py`.

Quick start from this folder:

```sh
JMPPED.bat status
JMPPED.bat bake --copy
python jmpped_selftest.py --selftest
```

See `docs/PEDESTRIAN-AUTHORING.md` for the full assembly map and `NAMING.md`
for the workspace contract.

