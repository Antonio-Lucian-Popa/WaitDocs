from PIL import Image

base = Image.open("waitdocs_1024.png").convert("RGBA")
base.save(
    "waitdocs.ico",
    sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)],
)
