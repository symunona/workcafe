import os
import requests

def download_image(url, save_path, headers=None):
    try:
        if not headers:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            with open(save_path, 'wb') as f:
                f.write(response.content)
            if not os.path.exists(save_path) or os.path.getsize(save_path) == 0:
                if os.path.exists(save_path):
                    os.remove(save_path)
                print(f"download_image: file missing or empty after write: {save_path}")
                return False
            return True
    except Exception as e:
        print(f"Error downloading {url}: {e}")
    return False
