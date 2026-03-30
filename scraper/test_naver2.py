from playwright.sync_api import sync_playwright
import json

def test_naver():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        def handle_response(response):
            if "api/search/allSearch" in response.url:
                print(f"URL: {response.url}")
                print(f"Status: {response.status}")
                try:
                    data = response.json()
                    print("JSON parsed successfully")
                except Exception as e:
                    print("Error parsing JSON:", e)

        page.on("response", handle_response)

        lat = 37.5665
        lon = 126.9780
        coord_url = f"https://map.naver.com/p?c=15.00,0,0,0,dh&lat={lat}&lng={lon}"
        print("Navigating to:", coord_url)
        page.goto(coord_url)
        page.wait_for_timeout(2000)

        url = "https://map.naver.com/p/search/%EC%B9%B4%ED%8E%98?c=15.00,0,0,0,dh"
        print("Navigating to:", url)
        page.goto(url)
        page.wait_for_timeout(5000)

        browser.close()

if __name__ == "__main__":
    test_naver()
