import pandas as pd
import numpy as np
import datetime
now = datetime.datetime.now()   

YoutubeProfiles = pd.read_csv(f'data/profiles/profiles{now.year}-{now.month}.csv', index_col=0)
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

STinyChannels.to_csv(f'data/{now.year}-{now.month}newviewsSTinyexport.csv')
TinyChannels.to_csv(f'data/{now.year}-{now.month}newviewstinyexport.csv')
SuperSmallChannels.to_csv(f'data/{now.year}-{now.month}newviewsupersmallexport.csv')
SmallChannels.to_csv(f'data/{now.year}-{now.month}newviewssmallexport.csv')
MediumChannels.to_csv(f'data/{now.year}-{now.month}newviewssmediumexport.csv')
LargeChannels.to_csv(f'data/{now.year}-{now.month}newviewslargeexport.csv')
MassiveChannels.to_csv(f'data/{now.year}-{now.month}newviewsmassiveexport.csv')

STinyChannels = STinyChannels.sort_values('suscribersChange', ascending=False)
TinyChannels = TinyChannels.sort_values('suscribersChange', ascending=False)
SuperSmallChannels = SuperSmallChannels.sort_values('suscribersChange', ascending=False)
SmallChannels = SmallChannels.sort_values('suscribersChange', ascending=False)
MediumChannels = MediumChannels.sort_values('suscribersChange', ascending=False)
LargeChannels= LargeChannels.sort_values('suscribersChange', ascending=False)
MassiveChannels = MassiveChannels.sort_values('suscribersChange', ascending=False)

STinyChannels.to_csv(f'data/{now.year}-{now.month}suscribersChangesSTinyexport.csv')
TinyChannels.to_csv(f'data/{now.year}-{now.month}suscribersChangestinyexport.csv')
SuperSmallChannels.to_csv(f'data/{now.year}-{now.month}suscribersChangesupersmallexport.csv')
SmallChannels.to_csv(f'data/{now.year}-{now.month}suscribersChangesmallexport.csv')
MediumChannels.to_csv(f'data/{now.year}-{now.month}suscribersChangemediumexport.csv')
LargeChannels.to_csv(f'data/{now.year}-{now.month}suscribersChangelargeexport.csv')
MassiveChannels.to_csv(f'data/{now.year}-{now.month}suscribersChangemassiveexport.csv')


STinyChannels = STinyChannels.sort_values('earninghigh', ascending=False)
STinyChannels.to_csv(f'data/{now.year}-{now.month}earninghighSTinyexport.csv')
