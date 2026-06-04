import zipfile, os

with zipfile.ZipFile("deploy.zip", "w", zipfile.ZIP_DEFLATED) as zf:
    for dp, _, files in os.walk("deploy_pkg"):
        for f in files:
            full = os.path.join(dp, f)
            arc = os.path.relpath(full, "deploy_pkg").replace(os.sep, "/")
            if arc.endswith(".sh") or "/hooks/" in arc:
                # Shell scripts: LF endings + executable permissions
                with open(full, "rb") as src:
                    content = src.read().replace(b"\r\n", b"\n")
                info = zipfile.ZipInfo(arc)
                info.external_attr = 0o755 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, content)
            else:
                zf.write(full, arc)
print("zipped", os.path.getsize("deploy.zip"))
