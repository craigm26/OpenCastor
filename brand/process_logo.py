import os

import matplotlib.colors as mcolors
import numpy as np
from PIL import Image, ImageColor


def process_logo(input_path, output_transparent, output_inverse):
    print(f"Loading {input_path}")
    img = Image.open(input_path).convert("RGBA")
    data = np.array(img).astype(float)

    # We assume the top-left pixel is the background color
    bg_color = data[0, 0, :3]

    r, g, b, _a = data[:, :, 0], data[:, :, 1], data[:, :, 2], data[:, :, 3]

    # Distance from background color
    dist = np.sqrt((r - bg_color[0]) ** 2 + (g - bg_color[1]) ** 2 + (b - bg_color[2]) ** 2)

    # Map distance to alpha (smooth transition for anti-aliasing)
    # Background has small dist. Let dist < 15 be fully transparent.
    alpha = np.clip((dist - 15) / 45 * 255, 0, 255)

    # Fix the edge halo by replacing background color with white but keeping alpha
    transparent_data = data.copy()
    transparent_data[:, :, :3] = np.where(
        alpha[:, :, None] < 200, 255.0, transparent_data[:, :, :3]
    )  # Soften edges towards white
    transparent_data[:, :, 3] = alpha

    Image.fromarray(transparent_data.astype(np.uint8)).save(output_transparent)
    print(f"Saved {output_transparent}")

    # Create the inverse colored image (dark squirrel on transparent)
    rgb_norm = transparent_data[:, :, :3] / 255.0
    hsv = mcolors.rgb_to_hsv(rgb_norm)

    # Invert the Value (brightness) channel, but only for pixels that have low saturation
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    # Cyan is around Hue=0.5 to 0.6. Let's find white/gray pixels (low saturation)
    # and invert their value.
    # We also want to map white to a dark blue (#0a0b1e)
    is_cyan = (h > 0.45) & (h < 0.65) & (s > 0.3)

    dark_blue_rgb = np.array(ImageColor.getrgb("#0a0b1e"))[:3] / 255.0
    dark_blue_hsv = mcolors.rgb_to_hsv(dark_blue_rgb)

    v_new = 1.0 - v

    # Blend: anything that's not cyan becomes dark blue tinted.
    h_final = np.where(is_cyan, h, dark_blue_hsv[0])
    s_final = np.where(is_cyan, s, dark_blue_hsv[1])

    # Adjust value so it hits #0a0b1e
    v_final = np.where(is_cyan, v, v_new)
    # Further push the "white" regions (now very dark) to #0a0b1e's V
    v_final = np.where((~is_cyan) & (v_final < 0.2), dark_blue_hsv[2] + v_final, v_final)

    hsv_new = np.dstack((h_final, s_final, v_final))
    rgb_new = mcolors.hsv_to_rgb(hsv_new) * 255.0

    inverse_data = transparent_data.copy()
    inverse_data[:, :, :3] = rgb_new

    Image.fromarray(inverse_data.astype(np.uint8)).save(output_inverse)
    print(f"Saved {output_inverse}")


if __name__ == "__main__":
    brain_dir = r"C:\Users\CraigM\.gemini\antigravity\brain\68da367e-3972-4069-98bc-0c41c0a9f68a"
    preview_img = os.path.join(brain_dir, "opencastor_logo_preview_1773426887664.png")

    output_trans = os.path.join(brain_dir, "opencastor_logo_transparent.png")
    output_inv = os.path.join(brain_dir, "opencastor_logo_inverse.png")

    process_logo(preview_img, output_trans, output_inv)
