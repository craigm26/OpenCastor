import os

from PIL import Image


def generate_assets(source_img_path, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    print(f"Generating assets from {source_img_path} into {output_dir}")

    img = Image.open(source_img_path).convert("RGBA")

    # 1. Generate icon PNGs
    sizes = [64, 128, 192, 256, 512, 1024]
    for size in sizes:
        resized = img.resize((size, size), Image.Resampling.LANCZOS)
        out_path = os.path.join(output_dir, f"icon-{size}.png")
        resized.save(out_path)
        print(f"Created {out_path}")

    # 2. Apple and Android specific
    img.resize((180, 180), Image.Resampling.LANCZOS).save(
        os.path.join(output_dir, "apple-touch-icon.png")
    )
    img.resize((192, 192), Image.Resampling.LANCZOS).save(
        os.path.join(output_dir, "android-chrome-192.png")
    )
    img.resize((512, 512), Image.Resampling.LANCZOS).save(
        os.path.join(output_dir, "android-chrome-512.png")
    )

    # 3. Favicon (multi-size ICO)
    icon_sizes = [(16, 16), (32, 32), (48, 48), (64, 64)]
    img.save(os.path.join(output_dir, "favicon.ico"), format="ICO", sizes=icon_sizes)
    print("Created favicon.ico")


if __name__ == "__main__":
    brain_dir = r"C:\Users\CraigM\.gemini\antigravity\brain\68da367e-3972-4069-98bc-0c41c0a9f68a"
    brand_dir = r"c:\Users\CraigM\source\repos\OpenCastor\brand"

    trans_img = os.path.join(brain_dir, "opencastor_logo_transparent.png")
    inv_img = os.path.join(brain_dir, "opencastor_logo_inverse.png")

    generate_assets(trans_img, os.path.join(brand_dir, "transparent"))
    generate_assets(inv_img, os.path.join(brand_dir, "inverse"))

    # Also overwrite the light theme assets in site/assets if we want the light theme to use inverse
    site_assets = r"c:\Users\CraigM\source\repos\OpenCastor\site\assets"
    os.makedirs(site_assets, exist_ok=True)

    # site/assets uses logo-white.svg and icon.svg. Since we don't have SVG, let's just make sure we have PNGs
    # However we did create icon.svg using base64 wrapper earlier for the main logo.
    # Let's create an icon-inverse.svg wrapping the inverse PNG so the website can use it easily if needed.
    import base64

    def create_svg_wrapper(png_path, svg_path):
        with open(png_path, "rb") as f:
            b64_data = base64.b64encode(f.read()).decode("utf-8")

        svg_content = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
  <image href="data:image/png;base64,{b64_data}" x="0" y="0" width="1024" height="1024" />
</svg>"""
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(svg_content)
        print(f"Created {svg_path}")

    # Generate SVG wrappers for the 1024 variants
    trans_1024 = os.path.join(brand_dir, "transparent", "icon-1024.png")
    inv_1024 = os.path.join(brand_dir, "inverse", "icon-1024.png")

    create_svg_wrapper(trans_1024, os.path.join(brand_dir, "transparent", "icon-transparent.svg"))
    create_svg_wrapper(inv_1024, os.path.join(brand_dir, "inverse", "icon-inverse.svg"))
