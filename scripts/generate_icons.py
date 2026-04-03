import os
import sys

try:
    from PIL import Image
except ImportError:
    print("Pillow not installed. Cannot resize images.", file=sys.stderr)
    sys.exit(1)

master_image_path = r"C:\Users\CraigM\.gemini\antigravity\brain\68da367e-3972-4069-98bc-0c41c0a9f68a\opencastor_logo_preview_1773426887664.png"
brand_dir = r"c:\Users\CraigM\source\repos\OpenCastor\brand"
site_assets_dir = r"c:\Users\CraigM\source\repos\OpenCastor\site\assets"


def generate_pngs():
    img = Image.open(master_image_path).convert("RGBA")

    # Save the 1024 master
    img.resize((1024, 1024), Image.Resampling.LANCZOS).save(
        os.path.join(brand_dir, "icon-1024.png")
    )

    sizes = [64, 128, 192, 256, 512]
    for size in sizes:
        resized = img.resize((size, size), Image.Resampling.LANCZOS)
        resized.save(os.path.join(brand_dir, f"icon-{size}.png"))

        if size == 64:
            # Also save a 64x64 favicon
            resized.convert("RGB").save(os.path.join(brand_dir, "favicon.ico"), format="ICO")
            resized.save(os.path.join(site_assets_dir, "favicon.ico"), format="ICO")
            resized.save(os.path.join(site_assets_dir, "icon-64.png"))

    # Also generate the android/apple exact named ones
    img.resize((192, 192), Image.Resampling.LANCZOS).save(
        os.path.join(brand_dir, "android-chrome-192.png")
    )
    img.resize((512, 512), Image.Resampling.LANCZOS).save(
        os.path.join(brand_dir, "android-chrome-512.png")
    )
    img.resize((180, 180), Image.Resampling.LANCZOS).save(
        os.path.join(brand_dir, "apple-touch-icon.png")
    )

    img.resize((192, 192), Image.Resampling.LANCZOS).save(
        os.path.join(site_assets_dir, "android-chrome-192.png")
    )
    img.resize((512, 512), Image.Resampling.LANCZOS).save(
        os.path.join(site_assets_dir, "android-chrome-512.png")
    )
    img.resize((180, 180), Image.Resampling.LANCZOS).save(
        os.path.join(site_assets_dir, "apple-touch-icon.png")
    )

    print("Successfully generated all PNG assets.")


if __name__ == "__main__":
    generate_pngs()
