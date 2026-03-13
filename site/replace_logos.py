import os
import re

site_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)))
html_files = [f for f in os.listdir(site_dir) if f.endswith('.html')]

# The replacement HTML for the logo
new_logo_html = '<a href="/" class="nav-logo">\n      <div class="nav-logo-img"></div>\n      <span>OpenCastor</span>\n    </a>'

for f in html_files:
    path = os.path.join(site_dir, f)
    with open(path, 'r', encoding='utf-8') as file:
        content = file.read()
        
    # Replace any <a href="/" class="nav-logo">...<span>OpenCastor</span>...</a>
    new_content = re.sub(
        r'<a href="/" class="nav-logo">.*?<span>OpenCastor</span>\s*</a>',
        new_logo_html,
        content,
        flags=re.DOTALL
    )
    
    # Also handle the footer inline SVG
    new_content = re.sub(
        r'<a href="/" class="nav-logo"><svg viewBox="0 0 32 32".*?</svg><span>OpenCastor</span></a>',
        new_logo_html,
        new_content,
        flags=re.DOTALL
    )
    
    if new_content != content:
        with open(path, 'w', encoding='utf-8') as file:
            file.write(new_content)
        print(f"Updated {f}")
