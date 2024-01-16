import os
import csv
from datetime import datetime

# Download ROV data from processed/dive_reports/[DIVE]/merged/[DIVE].NAV3D.M1.sampled.tsv

# Step 1: Read TSV file and extract relevant columns

tsv_folder = r"G:\Shared drives\Production\2024 Working Folder\RUMI\H2021\raw\Zeuss\images"
tsv_filename = "H2021.NAV3D.M1.sampled.tsv"
for filename in os.listdir(tsv_folder):
    if filename.endswith(".tsv"):
        tsv_filename = os.path.join(tsv_folder, filename)
        break

if tsv_filename:
    with open(tsv_filename, "r") as tsvfile:
        reader = csv.reader(tsvfile, delimiter='\t')
        next(reader)  # Skip the header row if present
        data_rows = []
        for row in reader:
            data_rows.append({
                "TIME": datetime.fromisoformat(row[0]),
                "LAT": row[1],
                "LONG": row[2],
                "DEPTH": row[3]
            })
else:
    print("TSV file not found.")
    exit()

# Step 2: Read image files in the folder
image_folder = tsv_folder
image_files = [filename for filename in os.listdir(image_folder) if filename.endswith(".png")]

# Step 3: Extract timestamp from image filenames
image_data = []
for image_file in image_files:
    timestamp_str = image_file[:15]
    image_data.append({
        "FILENAME": image_file,
        "TIMESTAMP": datetime.strptime(timestamp_str, "%Y%m%dT%H%M%S")
    })

# Step 4: Compare timestamps and estimate locations
matches = 0
reference_depth = 0  # Mean sea level
for image in image_data:
    closest_match = min(data_rows, key=lambda row: abs(row["TIME"] - image["TIMESTAMP"]))
    image["LAT_EST"] = closest_match["LAT"]
    image["LONG_EST"] = closest_match["LONG"]
    image["ALTITUDE_EST"] = reference_depth - float(closest_match["DEPTH"])
    matches += 1

# Step 5: Generate flight log file
flight_log_filename = os.path.join(tsv_folder, "flight_log.txt")
unique_locations = set()
with open(flight_log_filename, "w") as f:
    for image in image_data:
        line = "{};{};{};{}".format(image["FILENAME"], image["LAT_EST"], image["LONG_EST"], image["ALTITUDE_EST"])
        if line not in unique_locations:
            f.write(line + "\n")
            unique_locations.add(line)

# Print summary
print("Files examined: {}".format(len(image_files)))
print("Data rows interpreted: {}".format(len(data_rows)))
print("Matches made: {}".format(matches))
