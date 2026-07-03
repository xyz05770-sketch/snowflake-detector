from pathlib import Path
import shutil

# Files to delete
files = [
    "semantic_view.sql",
    "semantic_view_review.json",
]

for file in files:
    path = Path(file)
    try:
        path.unlink()
        print(f"Deleted: {file}")
    except FileNotFoundError:
        print(f"File not found: {file}")

# Clear all contents of the output directory while keeping the directory itself
output_dir = Path("snowflake_inventory_output")

if output_dir.exists() and output_dir.is_dir():
    for item in output_dir.iterdir():
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            print(f"Deleted: {item}")
        except Exception as e:
            print(f"Failed to delete {item}: {e}")
else:
    print(f"Directory not found: {output_dir}")