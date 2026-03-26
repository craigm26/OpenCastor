import base64
import os

brand_dir = r"c:\Users\CraigM\source\repos\OpenCastor\brand"
icon_path = os.path.join(brand_dir, "icon-512.png")

with open(icon_path, "rb") as f:
    b64 = base64.b64encode(f.read()).decode("utf-8")

icon_svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" aria-label="OpenCastor icon">
  <image href="data:image/png;base64,{b64}" width="512" height="512" />
</svg>"""

with open(os.path.join(brand_dir, "icon.svg"), "w", encoding="utf-8") as f:
    f.write(icon_svg)
with open(os.path.join(r"c:\Users\CraigM\source\repos\OpenCastor\site\assets", "icon.svg"), "w", encoding="utf-8") as f:
    f.write(icon_svg)

lockup_svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 120" role="img" aria-label="OpenCastor logo">
  <defs>
    <linearGradient id="text-grad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0ea5e9"/>
      <stop offset="100%" stop-color="#2dd4bf"/>
    </linearGradient>
  </defs>

  <!-- ── Icon ── -->
  <g transform="translate(16, 12)">
    <image href="data:image/png;base64,{b64}" width="96" height="96" />
  </g>

  <!-- ── Wordmark ── -->
  <g transform="translate(128, 56)">
    <text font-family="'Inter', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif"
          font-weight="800" font-size="44" letter-spacing="-0.5">
      <tspan fill="#0a0b1e">Open</tspan><tspan fill="url(#text-grad)">Castor</tspan>
    </text>
    <text x="2" y="32"
          font-family="'Inter', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif"
          font-weight="500" font-size="14" fill="#6b7280" letter-spacing="0.5">
      UNIVERSAL RUNTIME FOR EMBODIED AI
    </text>
  </g>
</svg>"""

with open(os.path.join(brand_dir, "lockup.svg"), "w", encoding="utf-8") as f:
    f.write(lockup_svg)

# And a dark-background variant lockup
lockup_white_svg = lockup_svg.replace('fill="#0a0b1e">Open', 'fill="#ffffff">Open')

with open(os.path.join(r"c:\Users\CraigM\source\repos\OpenCastor\site\assets", "logo-white.svg"), "w", encoding="utf-8") as f:
    f.write(lockup_white_svg)

print("SVGs generated!")
