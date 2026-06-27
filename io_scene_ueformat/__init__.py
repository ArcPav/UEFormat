import zstandard as zstd

from . import op

bl_info = {
    "name": "UE Format (.uemodel / .ueanim / .uepose)",
    "author": "Half & ArcPav(AI)",
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > UE Format",
    "category": "Import",
}


global zstd_decompressor
zstd_decompressor = zstd.ZstdDecompressor()

def register() -> None:
    global zstd_decompressor  # noqa: PLW0603
    zstd_decompressor = zstd.ZstdDecompressor()

    op.register()


def unregister() -> None:
    op.unregister()

    global zstd_decompressor  # noqa: PLW0603
    del zstd_decompressor


if __name__ == "__main__":
    register()
