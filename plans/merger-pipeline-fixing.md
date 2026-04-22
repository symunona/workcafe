Do you see in git history, that previously we tried fixing the merger pipeline.

Investigate.

The following area is our test area:

Around this starbucks:

https://workcafe.tmpx.space/cafe/ea70d7b0-d811-5f06-805d-1e45a63a1c46

This merged fine this time, but the neighbor compose cafe did not. There are 3 separate entries:
google: https://workcafe.tmpx.space/cafe/e3a32e70-2bfc-5cca-b35d-7235e9b53571
kakao: https://workcafe.tmpx.space/cafe/29275baf-c4ab-56a9-947d-f8ecc359a3e0
osm: https://workcafe.tmpx.space/cafe/7f6d2bc6-9382-51d3-84d6-36eb4bcba2e2

Their englishified names are all storing compose cafe, so I do not understand why that happened.

Also, around 500m south east, this starbucks:
kakao & naver matched: https://workcafe.tmpx.space/cafe/3ed30aca-1ebc-56eb-a229-074bc059dc80
osm did not: https://workcafe.tmpx.space/cafe/da7e2d9a-9ad3-5ad4-bcd0-88e2c7a0904c

Or this mega:
kakao: https://workcafe.tmpx.space/cafe/523d42bc-ef63-5df0-8e44-7527f5b605dd
osm: https://workcafe.tmpx.space/cafe/74496187-5c87-546b-aab4-486d78286027

or this starbucks:
kakao: https://workcafe.tmpx.space/cafe/54e75b88-54c6-5c57-bdbb-992f413c2a1c
osm: https://workcafe.tmpx.space/cafe/5cd29671-3dec-50b6-a65f-3f738ccc95bc

or this random cafe Deezer 39:
kakao: https://workcafe.tmpx.space/cafe/4f1a95f0-dbf0-5774-bba1-d789b8d0af6b
osm: https://workcafe.tmpx.space/cafe/77f4beb8-1b92-5e50-ad18-f92214cb01b9

Let's create a small test case for this.
Create `just test-pipeline`, that:
takes this area (max 1km around, maybe even less, find the optimal) and write a tests evaluator for this area.

You are a smart AI, you can do the matching based on this data, and find other ones that are similar:
you'd probably do something like export for yourself a bunch of coordinate pairs with their names and decide that they should be one group.
Look up these one by one in the DB
Then make a test case that these and these should be better: create a wired in test script - you can use their orig ids, we do not modify them and they're consistent.

Based on that, investigate, why our algo does not find the match, and improve upon the algo.

Please back up each pipeline version's files so I can manually look at it in the history modal we just made on the top right with the result, and write your conclusions in the notes.

Let's iterate!