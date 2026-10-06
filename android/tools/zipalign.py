"""zipalign in Python: stored (uncompressed) entries start on a 4-byte boundary, which Android needs for resources.arsc."""
import sys
import zipfile


def align(src, dst, n=4):
    zin = zipfile.ZipFile(src)
    with zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            new = zipfile.ZipInfo(info.filename, date_time=(2020, 1, 1, 0, 0, 0))
            new.external_attr = info.external_attr
            new.compress_type = zipfile.ZIP_STORED if info.filename == "resources.arsc" or info.filename.endswith(".png") else zipfile.ZIP_DEFLATED
            if new.compress_type == zipfile.ZIP_STORED:
                # local header = 30 bytes + name; pad the 'extra' field so the data lands on a boundary
                offset = zout.fp.tell() + 30 + len(new.filename.encode())
                new.extra = b"\0" * ((n - offset % n) % n)
            zout.writestr(new, data)


if __name__ == "__main__":
    align(sys.argv[1], sys.argv[2])
