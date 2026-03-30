
Let's read README.md

Let's plan, implement and test the scraper part.

make a dir called data/seoul/

create separate dirs for the separate datasources.
create a cafedata.db sqlite db in there, but also get all the scraped data into subfolders like:
data/seould/[provider]/[cafe_id]/cafe.json

If you scrape images put it to
data/seould/[provider]/[cafe_id]/images

Make sure the scraper is not trying to load from scratch.
Go from the center of seoul in a spiral.

Create a simple way to track progress.

Come up with coverage strategies and explain them in STRATEGIES.md!

Iterate until you get some data, do try it until it works and you can reliably tell if it's running.

If you get 429, come up wit strategies to counteract. You can set up a local tor proxy, actually, please use that by default for scraping!

Consider using headless browsers for scraping if there are no open API accesses.

Let me know what you concluded!


