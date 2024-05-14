import pandas as pd
import numpy as np

YoutubeProfiles = pd.read_csv('data\profiles\profiles2024-5.csv', index_col=0)
YoutubeProfiles.head()
YoutubeProfiles['subs'] = pd.to_numeric(YoutubeProfiles['subs'], errors='coerce')



SmallChannels = YoutubeProfiles[YoutubeProfiles['subs'] < 500000]
MediumChannels = YoutubeProfiles[(YoutubeProfiles['subs'] > 500000) & (YoutubeProfiles['subs'] < 1000000)]
LargeChannels= YoutubeProfiles[(YoutubeProfiles['subs'] > 1000000) & (YoutubeProfiles['subs'] < 3000000)]
MassiveChannels = YoutubeProfiles[YoutubeProfiles['subs'] > 3000000]
SmallChannels = SmallChannels.sort_values('monthly_change_subs', ascending=False)
SmallChannels.reset_index(drop=True, inplace=True)
pd.set_option('display.max_columns', 100)
print(SmallChannels.head())

SmallChannels = SmallChannels.sort_values('newsubs', ascending=False)
MediumChannels = MediumChannels.sort_values('newsubs', ascending=False)
LargeChannels= LargeChannels.sort_values('newsubs', ascending=False)
MassiveChannels = MassiveChannels.sort_values('newsubs', ascending=False)
SmallChannels.to_csv('data/newsubssmallexport.csv')
MediumChannels.to_csv('data/newsubsmediumexport.csv')
LargeChannels.to_csv('data/newsubslargeexport.csv')
MassiveChannels.to_csv('data/newsubsmassiveexport.csv')