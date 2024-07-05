from src.data.data_pull import DataPull
import geopandas as gpd
import pandas as pd
import polars as pl
import numpy as np
import os


class DataProcess(DataPull):
    def __init__(self, debug=False):
        super().__init__()
        self.debug = debug
        # self.blocks = self.process_shps()
        # self.process_lodes()
        self.process_roads()

    def process_shps(self) -> pl.DataFrame:
        empty_df = [
            pl.Series("STATEFP20", [], dtype=pl.String),
            pl.Series("GEOID20", [], dtype=pl.String),
            pl.Series("lon", [], dtype=pl.Float64),
            pl.Series("lat", [], dtype=pl.Float64),
        ]
        blocks = pl.DataFrame(empty_df).clear()
        if not os.path.exists("data/processed/blocks.parquet"):
            for state, name in self.codes.select(pl.col("fips", "state_name")).rows():
                file_name = f"data/shape_files/block_{name}_{str(state).zfill(2)}.zip"
                tmp = gpd.read_file(file_name, engine="pyogrio")
                tmp = tmp.set_crs(3857, allow_override=True)
                tmp_shp = tmp[["STATEFP20", "GEOID20", "geometry"]].copy()
                tmp_shp["centroid"] = tmp_shp.centroid
                tmp_shp["lon"] = tmp_shp.centroid.x
                tmp_shp["lat"] = tmp_shp.centroid.y
                tmp_block = pl.from_pandas(
                    tmp_shp[["STATEFP20", "GEOID20", "lon", "lat"]]
                )
                blocks = pl.concat([blocks, tmp_block], how="vertical")
                print(
                    "\033[0;36mPROCESS: \033[0m" + f"Finished processing {name} Shapes"
                )
            blocks.sort(by=["STATEFP20", "GEOID20"]).write_parquet(
                "data/processed/blocks.parquet"
            )
            return blocks
        else:
            return pl.read_parquet("data/processed/blocks.parquet")

    def process_lodes(self):
        empty_df = [
            pl.Series("state", [], dtype=pl.String),
            pl.Series("fips", [], dtype=pl.String),
            pl.Series("state_abbr", [], dtype=pl.String),
            pl.Series("year", [], dtype=pl.Int64),
            pl.Series("avg_distance", [], dtype=pl.Float64),
        ]
        lodes = pl.DataFrame(empty_df).clear()
        if not os.path.exists("data/processed/lodes.parquet"):
            for state, name, fips in self.codes.select(
                pl.col("state_abbr", "state_name", "fips")
            ).rows():
                for year in range(2005, 2020):
                    file_name = f"data/raw/lodes_{state}_{year}.csv.gz"
                    try:
                        value = self.process_block(file_name)
                    except:
                        print(
                            "\033[1;33mWARNING:  \033[0m"
                            + f"Failed to process lodes_{name}_{state}_{year}"
                        )
                        continue
                    tmp_df = pl.DataFrame(
                        [
                            pl.Series("state", [state], dtype=pl.String),
                            pl.Series("fips", [fips], dtype=pl.String),
                            pl.Series("state_abbr", [name], dtype=pl.String),
                            pl.Series("year", [year], dtype=pl.Int64),
                            pl.Series("avg_distance", [value], dtype=pl.Float64),
                        ]
                    )
                    lodes = pl.concat([lodes, tmp_df], how="vertical")
                    if self.debug:
                        print(
                            "\033[0;36mINFO: \033[0m"
                            + f"Finished processing lodes {name} for {year}"
                        )
            lodes.sort(by=["state", "year"]).write_parquet(
                "data/processed/lodes.parquet"
            )

    def process_acs(self):
        empty_df = [
            pl.Series("year", [], dtype=pl.Date),
            pl.Series("state", [], dtype=pl.Int64),
            pl.Series("PUMA", [], dtype=pl.Int64),
            pl.Series("avg_time", [], dtype=pl.Float64),
            pl.Series("sex", [], dtype=pl.Int32),
            pl.Series("race", [], dtype=pl.String),
        ]
        acs = pl.DataFrame(empty_df).clear()

        for file in os.listdir("data/raw"):
            if file.startswith("acs"):
                original = pl.read_parquet(f"data/raw/{file}")
                for sex in [0, 1, 3]:
                    for race in [
                        "RACAIAN",
                        "RACASN",
                        "RACBLK",
                        "RACNUM",
                        "RACWHT",
                        "RACSOR",
                        "HISP",
                        "ALL",
                    ]:
                        df = original
                        if not sex == 3:
                            df = df.filter(pl.col("SEX") == sex)
                        if not race == "ALL":
                            df = df.filter(pl.col(race) == 1)
                        df = df.select("year", "state", "PUMA", "PWGTP", "JWMNP")
                        df = df.with_columns(
                            total_time=(pl.col("PWGTP") * pl.col("JWMNP"))
                        )
                        df = df.group_by("year", "state", "PUMA").agg(
                            pl.col("PWGTP", "total_time").sum()
                        )
                        df = df.select(
                            "year",
                            "state",
                            "PUMA",
                            (pl.col("total_time") / pl.col("PWGTP")).alias("avg_time"),
                        )
                        df = df.with_columns(
                            pl.col("year").cast(pl.String).str.to_date("%Y"),
                            sex=sex,
                            race=pl.lit(race),
                        )
                        acs = pl.concat([acs, df], how="vertical")
        return acs

    def process_block(self, path) -> float:
        df = pl.read_csv(path, ignore_errors=True)
        df = df.rename({"S000": "total_jobs"}).select(
            pl.col("w_geocode", "h_geocode", "total_jobs")
        )

        dest = self.blocks.rename(
            {"GEOID20": "w_geocode", "lon": "w_lon", "lat": "w_lat"}
        )
        dest = dest.with_columns(
            (pl.col("w_geocode").cast(pl.Int64)).alias("w_geocode")
        )

        origin = self.blocks.rename(
            {"GEOID20": "h_geocode", "lon": "h_lon", "lat": "h_lat"}
        )
        origin = origin.with_columns(
            (pl.col("h_geocode").cast(pl.Int64)).alias("h_geocode")
        )

        df = df.join(origin, on="h_geocode", how="left")
        df = df.join(dest, on="w_geocode", how="left")
        df = df.with_columns(
            (
                6371.01
                * np.arccos(
                    np.sin(pl.col("h_lat")) * np.sin(pl.col("w_lat"))
                    + np.cos(pl.col("h_lat"))
                    * np.cos(pl.col("w_lat"))
                    * np.cos(pl.col("h_lon") - pl.col("w_lon"))
                )
            ).alias("distance")
        )
        df = df.filter(pl.col("distance") != np.nan)
        df = df.select(
            pl.col("distance").sum().alias("total_distance"),
            pl.col("total_jobs").sum().alias("total_jobs"),
        )

        value = df.select(
            (pl.col("total_distance") / pl.col("total_jobs")).alias("avg_distance")
        ).item()
        return value

    def process_roads(self):
        columns = ['linear_id', 'year', 'geometry']
        empty_data = {col: [] for col in columns}

        for year in range(2010, 2011):
            first_file_processed = False
            for file in os.listdir(f"data/shape_files/"):
                if file.startswith(f"roads_{year}"):
                    gdf = gpd.read_file(f"data/shape_files/{file}", engine="pyogrio")
                    gdf.rename(columns={"LINEARID": "linear_id"}, inplace=True)
                    gdf["year"] = pd.to_datetime(year, format="%Y")
                    gdf = gdf[["linear_id", "year", "geometry"]].set_crs(3857, allow_override=True)
                    
                    if not first_file_processed:
                        roads_df = gdf.copy()  # Initialize roads_df with the first valid gdf
                        first_file_processed = True
                    else:
                        roads_df = pd.concat([roads_df, gdf], ignore_index=True)
                        
                    print("\033[0;36mINFO: \033[0m" + f"Finished processing roads for {file}")
            
            roads_df.to_parquet(f"data/processed/roads_{year}.parquet")


if __name__ == "__main__":
    DataProcess()
