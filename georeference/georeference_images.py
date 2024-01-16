import os
import csv
from datetime import datetime

def get_directory_from_user():
    print("Please enter the path to the directory containing your images and TSV files:")
    directory = input()
    if not os.path.isdir(directory):
        print("Error: The entered path is not a directory or does not exist. Please try again.")
        exit()
    return directory

def get_tsv_from_folder(tsv_folder):
    tsv_filename = None
    for filename in os.listdir(tsv_folder):
        if filename.endswith(".tsv"):
            tsv_filename = os.path.join(tsv_folder, filename)
            break

    return tsv_filename

def georeference_images(tsv_filename, image_folder, output_path):
    if tsv_filename is None:
        print("Error: No TSV file found.")
        exit()

    # Read TSV file and extract data
    data_rows = []
    try:
        with open(tsv_filename, "r") as tsvfile:
            reader = csv.reader(tsvfile, delimiter='\t')
            next(reader)  # Skip the header row

            for row in reader:
                try:
                    data_rows.append({
                        "TIME": datetime.fromisoformat(row[0]),
                        "LAT": row[1],
                        "LONG": row[2],
                        "DEPTH": row[3]
                    })
                except Exception as e:
                    print(f"Error reading data from TSV file: {e}")
                    exit()

    except Exception as e:
        print(f"Error opening TSV file: {e}")
        exit()

    # Process image files
    image_files = [filename for filename in os.listdir(image_folder) if filename.endswith((".png", ".heif", ".jpg", ".jpeg"))] # Add more extensions if needed

    # Extract timestamp from image filenames and match with data
    image_data = []
    for image_file in image_files:
        try:
            # Assuming filenames are in the format 'P001C0019_20231023212955.heif'
            timestamp_str = image_file.split('_')[1].split('.')[0]
            image_timestamp = datetime.strptime(timestamp_str, "%Y%m%d%H%M%S")

            image_data.append({
                "FILENAME": image_file,
                "TIMESTAMP": image_timestamp
            })
        except Exception as e:
            print(f"Error processing filenames: {e}")
            continue

    # Compare timestamps and estimate locations
    matches = 0
    reference_depth = 0  # Mean sea level
    for image in image_data:
        closest_match = min(data_rows, key=lambda row: abs(row["TIME"] - image["TIMESTAMP"]))
        image["LAT_EST"] = closest_match["LAT"]
        image["LONG_EST"] = closest_match["LONG"]
        image["ALTITUDE_EST"] = reference_depth - float(closest_match["DEPTH"])
        matches += 1

    # Generate flight log file
    unique_locations = set()
    try:
        with open(output_path, "w") as f:
            for image in image_data:
                line = "{};{};{};{}".format(image["FILENAME"], image["LAT_EST"], image["LONG_EST"], image["ALTITUDE_EST"])
                if line not in unique_locations:
                    f.write(line + "\n")
                    unique_locations.add(line)
    except Exception as e:
        print(f"Error writing to flight log file: {e}")
        exit()

    # Print summary
    print(f"Files examined: {len(image_files)}")
    print(f"Data rows interpreted: {len(data_rows)}")
    print(f"Matches made: {matches}")

def main():
    # Get the directory from the user
    tsv_folder = get_directory_from_user()
    tsv_filename = get_tsv_from_folder(tsv_folder)

    image_folder = tsv_folder
    output_path = os.path.join(image_folder, "flight_log.txt")

    georeference_images(tsv_filename, image_folder, output_path)

# Run the main function
if __name__ == "__main__":
    main()
