"""将 PFS 图标转换为 installer/icon.ico（多尺寸）。"""

from pathlib import Path
import shutil
from PIL import Image

src = Path(__file__).parent.parent / "static" / "Images" / "pfs-mark.png"
dst = Path(__file__).parent / "icon.ico"
packaging_dst = Path(__file__).parent.parent / "packaging" / "pfs-mark.ico"

img = Image.open(src).convert("RGBA")
sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
img.save(dst, format="ICO", sizes=sizes)
shutil.copy2(dst, packaging_dst)
print(f"已生成: {dst}")
print(f"已同步: {packaging_dst}")
