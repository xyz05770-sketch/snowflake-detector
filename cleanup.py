from pathlib import Path
import shutil

# Files to delete
files = [
    "semantic_view.sql",
    "semantic_view_review.json",
]

# Directory whose contents get cleared (directory itself is kept)
output_dir = Path("snowflake_inventory_output")

print("This will permanently delete:")
for file in files:
    print(f"  - {file}")
print(f"  - everything inside {output_dir}/")
print("\n.env, input.json, and scope.json are NOT touched.")

confirm = input("\nContinue? [y/N] ").strip().lower()
if confirm != "y":
    print("Aborted, nothing was deleted.")
    raise SystemExit(0)

for file in files:
    path = Path(file)
    try:
        path.unlink()
        print(f"Deleted: {file}")
    except FileNotFoundError:
        print(f"File not found: {file}")

# Clear all contents of the output directory while keeping the directory itself
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
