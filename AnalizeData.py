import pandas as pd
import numpy as np

YoutubeProfiles = pd.read_csv('data\profiles\profiles2024-10.csv', index_col=0)
YoutubeProfiles.head()
YoutubeProfiles['view'] = pd.to_numeric(YoutubeProfiles['view'], errors='coerce')
YoutubeProfiles['suscribers'] = pd.to_numeric(YoutubeProfiles['suscribers'], errors='coerce')
YoutubeProfiles['viewChange'] = pd.to_numeric(YoutubeProfiles['viewChange'], errors='coerce')
YoutubeProfiles['suscribersChange'] = pd.to_numeric(YoutubeProfiles['suscribersChange'], errors='coerce')
YoutubeProfiles['earninghigh'] = pd.to_numeric(YoutubeProfiles['earninghigh'], errors='coerce')


STinyChannels =  YoutubeProfiles[(YoutubeProfiles['suscribers'] > 100000)]
TinyChannels =  YoutubeProfiles[(YoutubeProfiles['suscribers'] > 20000) & (YoutubeProfiles['suscribers'] < 100000)]
SuperSmallChannels = YoutubeProfiles[(YoutubeProfiles['suscribers'] > 50000) & (YoutubeProfiles['suscribers'] < 100000)]
SmallChannels = YoutubeProfiles[(YoutubeProfiles['suscribers'] > 100000) & (YoutubeProfiles['suscribers'] < 500000)]
MediumChannels = YoutubeProfiles[(YoutubeProfiles['suscribers'] > 500000) & (YoutubeProfiles['suscribers'] < 1000000)]
STinyChannels= YoutubeProfiles[(YoutubeProfiles['suscribers'] > 200000) & (YoutubeProfiles['suscribers'] < 400000)]
LargeChannels= YoutubeProfiles[(YoutubeProfiles['suscribers'] > 1000000) & (YoutubeProfiles['suscribers'] < 3000000)]
MassiveChannels = YoutubeProfiles[YoutubeProfiles['suscribers'] > 3000000]

pd.set_option('display.max_columns', 100)
print(SmallChannels.head())

STinyChannels = STinyChannels.sort_values('viewChange', ascending=False)
TinyChannels = TinyChannels.sort_values('viewChange', ascending=False)
SuperSmallChannels = SuperSmallChannels.sort_values('viewChange', ascending=False)
SmallChannels = SmallChannels.sort_values('viewChange', ascending=False)
MediumChannels = MediumChannels.sort_values('viewChange', ascending=False)
LargeChannels= LargeChannels.sort_values('viewChange', ascending=False)
MassiveChannels = MassiveChannels.sort_values('viewChange', ascending=False)

STinyChannels.to_csv('data/newviewsSTinyexport.csv')
TinyChannels.to_csv('data/newviewstinyexport.csv')
SuperSmallChannels.to_csv('data/newviewsupersmallexport.csv')
SmallChannels.to_csv('data/newviewssmallexport.csv')
MediumChannels.to_csv('data/newviewssmediumexport.csv')
LargeChannels.to_csv('data/newviewslargeexport.csv')
MassiveChannels.to_csv('data/newviewsmassiveexport.csv')

STinyChannels = STinyChannels.sort_values('suscribersChange', ascending=False)
TinyChannels = TinyChannels.sort_values('suscribersChange', ascending=False)
SuperSmallChannels = SuperSmallChannels.sort_values('suscribersChange', ascending=False)
SmallChannels = SmallChannels.sort_values('suscribersChange', ascending=False)
MediumChannels = MediumChannels.sort_values('suscribersChange', ascending=False)
LargeChannels= LargeChannels.sort_values('suscribersChange', ascending=False)
MassiveChannels = MassiveChannels.sort_values('suscribersChange', ascending=False)

STinyChannels.to_csv('data/suscribersChangesSTinyexport.csv')
TinyChannels.to_csv('data/suscribersChangestinyexport.csv')
SuperSmallChannels.to_csv('data/suscribersChangesupersmallexport.csv')
SmallChannels.to_csv('data/suscribersChangesmallexport.csv')
MediumChannels.to_csv('data/suscribersChangemediumexport.csv')
LargeChannels.to_csv('data/suscribersChangelargeexport.csv')
MassiveChannels.to_csv('data/suscribersChangemassiveexport.csv')


STinyChannels = STinyChannels.sort_values('earninghigh', ascending=False)
STinyChannels.to_csv('data/earninghighSTinyexport.csv')
