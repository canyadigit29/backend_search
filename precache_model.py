"""
Deprecated: model precache is disabled and this script is a no-op.

The CrossEncoder precache step was removed from the Dockerfile and
sentence-transformers is not installed in the runtime image.
Keeping this file as a harmless stub avoids accidental imports.
"""

def main() -> None:
    print("precache_model.py is deprecated; no-op.")


if __name__ == "__main__":
    main()

