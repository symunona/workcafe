from playwright.sync_api import sync_playwright

def test_naver_photo():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        place_id = "1328003630" # a known place id maybe? Or let's just use some random one
        url = f"https://pcmap.place.naver.com/restaurant/1328003630/photo"
        print("Navigating to:", url)
        resp = page.goto(url)
        print("Status:", resp.status)
        page.wait_for_timeout(2000)
        
        # print first few images
        images = page.evaluate('''() => {
            const imgElements = document.querySelectorAll('img');
            return Array.from(imgElements).map(img => img.src);
        }''')
        print(f"Found {len(images)} images")

        browser.close()

if __name__ == "__main__":
    test_naver_photo()
