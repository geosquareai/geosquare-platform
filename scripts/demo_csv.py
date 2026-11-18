"""Make a demo CSV from the grid file: one row per cell, a fake `demand` value."""
import sys
import geopandas as gpd

g = gpd.read_parquet(sys.argv[1])
out = g[["gid"]].copy()
out["demand"] = (g["coverage_ratio"] * 100).round(1)
out.to_csv(sys.argv[2], index=False)
print(len(out), "rows ->", sys.argv[2])
