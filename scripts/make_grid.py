"""
Step G1: 1 km GeoSquare V2 cells over Jabodetabek.

Uses geosquare-v2 1.0.0, with the signed ID registry (geosquare-registry-2026-09).
Level 9 is the 1 km cell for ID: side 1000 m on the grid CRS (scale error <= 0.61 % per the profile).

Area of interest: the 14 kabupaten and kota polygons in admin_jabodetabek_fixedgeo.parquet, dissolved.

Output: grid_cells_jabodetabek.parquet
  cell_uri, gid, x_idx, y_idx, level, coverage_ratio, geometry (EPSG:4326 cell polygon, clipped to AOI)

Usage:
    uv run --with "geosquare-v2[geo,batch,table,registry]==1.0.0" --with geopandas --with pyarrow --with shapely \
        python 01_grid_cells.py <admin_jabodetabek.parquet> <out_dir>
"""

from __future__ import annotations

import base64
import pathlib
import sys

import geopandas as gpd
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from geosquare_v2 import DbRegistryLoader, GeosquareService

LEVEL_1KM = 9
DOMAIN = "ID"
TRUST = {"geosquare-registry-2026-09": base64.b64decode("T6ikwn74d12UFk21VLKMFDhl/b+mLV6qo8MRHkg9E3Q=")}


def service() -> GeosquareService:
    return GeosquareService(DbRegistryLoader(TRUST).load())


def aoi_polygon(admin_path: str):
    admin = gpd.read_parquet(admin_path)
    if admin.crs is None or admin.crs.to_epsg() != 4326:
        admin = admin.to_crs(4326)
    return unary_union(admin.geometry.values), admin


def main(admin_path: str, out_dir: pathlib.Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    svc = service()
    aoi, admin = aoi_polygon(admin_path)
    print(f"AOI from {len(admin)} kab/kota, bounds {tuple(round(v, 3) for v in aoi.bounds)}")

    recs = svc.polygon_to_cells(
        DOMAIN, aoi, "EPSG:4326", LEVEL_1KM,
        output_geometry=True, geometry_crs="EPSG:4326", geometry_format="shapely",
    )
    print(f"cells intersecting AOI: {len(recs)}")

    rows = []
    for r in recs:
        geom = r.geometry
        clipped = geom.intersection(aoi)
        if clipped.is_empty:
            continue
        rows.append({
            "cell_uri": r.uri, "gid": r.gid, "x_idx": r.cell.x_idx, "y_idx": r.cell.y_idx,
            "level": r.cell.level, "coverage_ratio": r.coverage_ratio,
            "geometry": clipped,
        })
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    gdf["area_in_aoi_km2"] = gdf.to_crs(8857).area / 1e6
    gdf.to_parquet(out_dir / "grid_cells_jabodetabek.parquet", index=False)
    print(f"wrote {len(gdf)} cells; area in AOI {gdf['area_in_aoi_km2'].sum():,.0f} km2")
    print(gdf["area_in_aoi_km2"].describe().round(3).to_string())


if __name__ == "__main__":
    main(sys.argv[1], pathlib.Path(sys.argv[2]))
