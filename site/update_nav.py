import os
import re

site_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)))
html_files = [f for f in os.listdir(site_dir) if f.endswith('.html')]

links_html = """      <a href="/docs">Docs</a>
      <a href="/hardware">Hardware</a>
      <a href="/blog">Blog</a>
      <a href="/community">Community</a>
      <a href="/tutorials">Tutorials</a>
      <a href="/about">About</a>"""

nav_end_html = """    <div class="nav-end">
      <button class="theme-pill" id="themeToggle" aria-label="Toggle dark/light mode" title="Toggle theme">
        <span class="theme-pill-opt" data-opt="light" aria-label="Light mode"><svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg></span>
        <span class="theme-pill-opt" data-opt="dark" aria-label="Dark mode"><svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg></span>
      </button>
      <a href="https://github.com/craigm26/OpenCastor" class="btn-nav">GitHub <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M7 17L17 7M17 7H7M17 7V17"/></svg></a>
      <button class="mobile-toggle" aria-label="Menu"><span></span><span></span><span></span></button>
    </div>"""

for f in html_files:
    if f == "index.html":
        continue
        
    path = os.path.join(site_dir, f)
    with open(path, 'r', encoding='utf-8') as file:
        content = file.read()
        
    page_name = f.split('.')[0]
    page_links_html = links_html.replace(f'href="/{page_name}"', f'href="/{page_name}" class="active"')
    
    # regex from <div class="nav-links"> down to </nav>
    # Note: about.html has <div class="nav-theme-row">...</div> inside <div class="nav-links">
    new_content = re.sub(
        r'<div class="nav-links">.*?</nav>',
        f'<div class="nav-links">\n{page_links_html}\n    </div>\n{nav_end_html}\n  </div>\n</nav>',
        content,
        flags=re.DOTALL
    )
    
    with open(path, 'w', encoding='utf-8') as file:
        file.write(new_content)
        
    print(f"Updated {f}")
