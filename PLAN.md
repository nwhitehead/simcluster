# Create a SimCluster

the idea is a webpage where users can enter a bunch of bsky handles. the page itself gets follows of
those accounts, gets union of handles. shows the union, sorted by???

you can see how many handles total could be in the simcluster
oh, we get followers for each handle, go both directions, get all possible people in the simcluster

Can you make a SimCluster?
makes fun of the original simcluster paper, nathan in particular. talks about all my mistakes. sections discuss how to make a good community, what it takes, how i'm utterly failing. include cool quotes, like "you can seed me anytime", "i finally succeeded in networking socially"

##
The current project has a few pieces. One piece is a data analysis of the simcluster including
scripts, data summaries, and graphs. Another piece is the first paper that describes the simcluster,
shows the data, and describes various hypotheses.

There is also an extension related to "are you in the simcluster". This part is a paper with
description and analysis. It is also a standalone web page that lets bluesky users enter their
handle and see their "simcluster" score.

The new thing we're working on is writing a paper "Can you make a simcluster?" together with
a standalone webpage that lets users create simclusters, or score their belonging in other
users simclusters.

Features of standalone clustering tool:
* No backend needed, just uses public bsky apis
* Lets users add a list of bsky handles to a pool as seeds/hubs
* Each seed/hub they add gets processed to look at follows and followers
* Interface shows current # of seeds, # hubs, and total # handles in set
* Maybe show 