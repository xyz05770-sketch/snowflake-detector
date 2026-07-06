from pathlib import Path
import shutil
import time

# Directories whose contents get cleared (the directories themselves are kept)
output_dirs = [
    Path("snowflake_inventory_output"),
    Path("semantic_views_output"),
]

print("This will permanently delete everything inside:")
for output_dir in output_dirs:
    print(f"  - {output_dir}/")
print("\n.env, input.json, and scope.json are NOT touched.")

confirm = input("\nContinue? [y/N] ").strip().lower()
if confirm != "y":
    print("Aborted, nothing was deleted.")
    raise SystemExit(0)

def delete_with_retry(item, attempts=3, delay=0.3):
    """On Windows, shutil.rmtree can raise WinError 5 on the final rmdir even
    after successfully clearing a directory's contents - the OS briefly holds
    a lock on a just-emptied directory (Explorer/antivirus/indexer are common
    culprits). A short retry clears this without needing user intervention."""
    last_error = None
    for attempt in range(attempts):
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            return
        except Exception as e:
            last_error = e
            if attempt < attempts - 1:
                time.sleep(delay)
    raise last_error


# Clear all contents of each output directory while keeping the directory itself
for output_dir in output_dirs:
    if output_dir.exists() and output_dir.is_dir():
        for item in output_dir.iterdir():
            try:
                delete_with_retry(item)
                print(f"Deleted: {item}")
            except Exception as e:
                print(f"Failed to delete {item}: {e}")
    else:
        print(f"Directory not found: {output_dir}")
