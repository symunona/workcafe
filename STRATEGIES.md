# Coverage Strategies & Anti-429 Measures

## Coverage Strategy: Spiral Search

To ensure comprehensive coverage of a city like Seoul without duplicating effort, we employ a **Spiral Search Strategy**:

1. **Center Point Initialization**: Start at the geographic center of Seoul (e.g., Namsan Seoul Tower or Seoul City Hall coordinates).
2. **Grid/Radius Expansion**: Expand outward in a spiral or concentric circles. We can divide the map into a grid of coordinates.
3. **Bounding Box Queries**: For each point in the spiral, generate a bounding box or radius query to the mapping provider (e.g., OpenStreetMap via Overpass API, Kakao Maps, or Google Maps).
4. **Overlap Management**: Ensure slight overlaps between adjacent bounding boxes to avoid missing cafes on the edges.
5. **State Tracking**: Keep track of the last processed coordinate or grid cell in a local SQLite database or state file. This ensures the scraper can resume from where it left off instead of starting from scratch.

## Anti-429 (Rate Limiting) Measures

When scraping at scale, providers often return `429 Too Many Requests` or block IPs. To counteract this:

1. **Tor Proxy Rotation**: 
   - Route all scraping traffic through a local Tor proxy.
   - Periodically send a signal to the Tor control port to change the exit node (getting a new IP address) when a 429 error is encountered or after a certain number of requests.
2. **Request Delays and Jitter**:
   - Introduce random delays (jitter) between requests to mimic human behavior.
   - Example: `sleep(random.uniform(1.5, 3.5))` seconds between calls.
3. **Headless Browsers**:
   - For providers without open APIs or with strict API limits (like Google Maps or Kakao Maps), use headless browsers (e.g., Playwright or Selenium) to simulate real user interactions.
   - Implement realistic user agent rotation, viewport variations, and canvas fingerprint spoofing if necessary.
4. **Exponential Backoff**:
   - If a 429 error occurs, pause scraping, request a new Tor identity, and wait for an exponentially increasing amount of time (e.g., 2s, 4s, 8s) before retrying.
5. **Session Management**:
   - Clear cookies and local storage between sessions or when changing identities to avoid session-based tracking.
6. **Provider Diversification**:
   - Interleave requests across different providers (e.g., OpenStreetMap, then Kakao Maps) to distribute the load and reduce the frequency of requests to any single provider.
