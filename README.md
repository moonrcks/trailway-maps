# Trailway city maps

Offline maps of Ohio's biggest metros for [Trailway](https://moonrcks.github.io/trailway-site/), the Ohio bike route planner. Each pack holds a city's streets, sidewalks and trails from OpenStreetMap, sorted the way Trailway plans routes (protected paths, bike lanes, shared roads and sidewalks), in a compact format the app loads instantly. With a city downloaded (Settings › Map in the app), routes there plan right away instead of waiting on OpenStreetMap's servers.

The packs cover Akron, Canton, Cincinnati, Cleveland, Columbus, Dayton, Toledo and Youngstown. They're published as the [packs release](https://github.com/moonrcks/trailway-maps/releases/tag/packs), with a `manifest.json` that lists them.

## How they're made

`build_city_packs.py` reads each city's streets from OpenStreetMap (Geofabrik's Ohio extract in the workflow, or the Overpass API when run without `--pbf`) and writes `<city>.twpk` files and `manifest.json`. The City packs workflow runs it on the 2nd of every month and replaces the release files. To run it now, open Actions › City packs › Run workflow. To build locally:

```
pip install osmium
curl -LO https://download.geofabrik.de/north-america/us/ohio-latest.osm.pbf
python3 build_city_packs.py packs --pbf ohio-latest.osm.pbf [city ...]
```

The script is a copy of `tools/build_city_packs.py` from the Trailway app, which classifies streets the same way.

## License

Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright). The city packs are derived from OpenStreetMap and are available under the [Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
