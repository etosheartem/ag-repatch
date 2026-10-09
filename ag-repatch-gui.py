import sys

if "--privileged-patch" in sys.argv:
    from ag_repatch.privileged import main
else:
    from ag_repatch.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
