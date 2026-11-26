# Xueli theme atlas

`xueli-atlas.png` is a project-local decorative image generated with the built-in image_gen tool on 2026-10-16. It uses the user's approved D4 dashboard and supplied Xueli reference as visual inputs. No image-generation CLI or external asset service was used.

RGBA 1536×1024, transparent background, 3 columns × 2 rows. Cells: standing chibi, happy portrait, sleepy portrait / curious portrait, furry cat paws, star ribbon. CSS uses background-position without modifying the raster. Decorative elements are aria-hidden and never interactive. The 2 MB atlas is requested only by the two illustrated palettes.

Final generation prompt:

> Production web illustration sprite atlas, 1536x1024 landscape, TRUE transparent background. Exactly 3 equal columns by 2 equal rows, each cell square512x512, no visible grid, no labels, no text, no UI. Keep each subject centered wholly inside its own cell with generous 60px transparent padding, no crossing boundaries. Derive character identity from references: Xueli white long hair with pale pink tips, blue-green eyes, white star beret, tiny white cat ears, white/iceblue coat-dress and cape, little gold buttons, white sneakers. Same chibi character as D4 reference. Cute wholesome fully clothed UI decoration. TOP LEFT: complete standing mini chibi with white fluffy tail and blue-white outfit. TOP MIDDLE: happy closed-eye head and tiny hands peeking over invisible edge, white beret with stars. TOP RIGHT: sleepy face in white nightcap with pompom, tiny zzz shapes but no letters. BOTTOM LEFT: curious tilted head white beret, blue cardigan collar, tiny star. BOTTOM MIDDLE: a pair of soft white fluffy CAT paws, pink oval toe beans four per paw with central lobed pad, no claws, plush realistic-soft illustration as milk-pink UI corner decor. BOTTOM RIGHT: compact delicate white/cyan ribbon bow and three pale gold/aqua star ornaments, no character. Consistent delicate anime illustration quality, crisp silhouettes, no drop shadow outside items. Completely isolated transparent cells, ready for CSS background-position atlas usage. Do NOT include dashboards or screenshots, only six decor illustrations.

WebP delivery variant retains the PNG fallback. Rebuild with the existing Pillow dependency:

```python
from PIL import Image
with Image.open("static/theme-assets/xueli-atlas.png") as image:
    image.save("static/theme-assets/xueli-atlas.webp", "WEBP", quality=82, method=6)
```
