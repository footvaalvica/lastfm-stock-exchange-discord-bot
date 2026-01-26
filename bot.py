import os
import pylast
import discord
from datetime import datetime
from tinydb import TinyDB, Query

def calculate_user_money_sum(user):
    timestamp = datetime.now().date().isoformat().replace('-', '')
    user_entry = users_table.get(Query().username == user.get_name())
    user_money_sum = user_entry['money']
    last_updated = user_entry['last_updated']
    user_tracks = user_entry['user_tracks']

    daily_tracks = user.get_recent_tracks(now_playing=False, limit=None, time_from=last_updated)
    users_table.update({'last_updated': timestamp}, Query().username == user.get_name())

    if not daily_tracks:
        return user_money_sum
    
    for track in daily_tracks:
        print(f"Processing track: {track.track.artist.name} - {track.track.title}")
        artist = track.track.artist
        popularity = int(artist.get_listener_count())

        # Check if we have an entry for this artist on this date
        ArtistInfo = Query()
        artist_popularity_entry = artist_popularity_table.search((ArtistInfo.artist_name == artist.name))
        
        # if we don't have an entry for this artist, create one
        if not artist_popularity_entry:
            artist_popularity_table.insert({
                'artist_name': artist.name,
                'popularity': popularity,
                'timestamp': timestamp
            })
        
        # get the closest matching entry for this track date
        artist_popularity_entries = artist_popularity_table.search(ArtistInfo.artist_name == artist.name)
        artist_popularity_entry = max(artist_popularity_entries, key=lambda x: x['timestamp'] <= timestamp)
        popularity_difference = popularity - artist_popularity_entry['popularity'] + 1
        user_money_sum += popularity_difference
        users_table.update({'money': user_money_sum}, Query().username == user.get_name())

        if track.track.get_album() is not None:
            album_title = track.track.get_album().title
        else:
            album_title = "Single"
        user_tracks.append((track.track.artist.name, track.track.title, album_title, popularity_difference))
    
    users_table.update({'user_tracks': user_tracks}, Query().username == user.get_name())
    
    print(artist_popularity_table.all())
    print(users_table.all())
    return user_money_sum

def populate_artist_initial_popularity():
    top_artists = network.get_top_artists(limit=100)
    for artist in top_artists:
        artist_name = artist.item.name
        popularity = int(artist.item.get_listener_count())
        print(f"Populating {artist_name} with listener count {popularity} in {datetime.now().date().isoformat().replace('-', '')}")
        artist_popularity_table.insert({
            'artist_name': artist_name,
            'popularity': popularity,
            'timestamp': datetime.now().date().isoformat().replace('-', '')
        })

def required_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError("Missing required environment variable: " + name)
    return value


db = TinyDB('db.json')
API_KEY = required_env("LASTFM_API_KEY")
API_SECRET = required_env("LASTFM_API_SECRET")
network = pylast.LastFMNetwork(
    api_key=API_KEY,
    api_secret=API_SECRET,
)

artist_popularity_table = db.table('artist_popularity')
# # if artist_popularity_table.all() == []:
# #     populate_artist_initial_popularity()

users_table = db.table('users')
starting_time = 1769385600

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f'We have logged in as {client.user}')

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content.startswith('!money'):
        user_entry = users_table.get(Query().username == message.author.name)
        if not user_entry:
            await message.channel.send(f"{message.author.name}, set up your account first by setting your Last.fm username.")
            return
        user = network.get_user(user_entry['lastfm_username'])
        user_money_sum = calculate_user_money_sum(user)
        await message.channel.send(f"{message.author.name}, your total money is: {user_money_sum}")

    if message.content.startswith('!leaderboard'):
        all_users = users_table.all()
        leaderboard = []
        for entry in all_users:
            leaderboard.append((entry['username'], entry['money']))
        leaderboard.sort(key=lambda x: x[1], reverse=True)
        leaderboard_message = "Leaderboard:\n"
        for rank, (username, money) in enumerate(leaderboard, start=1):
            leaderboard_message += f"{rank}. {username}: {money}\n"
        await message.channel.send(leaderboard_message)

    if message.content.startswith('!setlastfm'):
        try:
            lastfm_username = message.content.split(' ')[1]
            user = network.get_user(lastfm_username)
            users_table.insert ({
                'username': message.author.name,
                'lastfm_username': lastfm_username,
                'money': 0,
                'last_updated': starting_time,
                'user_tracks': []
            })
            await message.channel.send(f"{message.author.name}, your Last.fm username has been set to {lastfm_username}.")
        except IndexError:
            await message.channel.send("Please provide a Last.fm username.")

    if message.content.startswith('!tracks'):
        user_entry = users_table.get(Query().username == message.author.name)
        if not user_entry:
            await message.channel.send(f"{message.author.name}, set up your account first by setting your Last.fm username.")
            return
        user_tracks = user_entry['user_tracks']
        if not user_tracks:
            await message.channel.send(f"{message.author.name}, you have no recorded tracks yet.")
            return
        tracks_message = "Your recorded tracks:\n"
        for artist, title, album, money in user_tracks:
                tracks_message += f"{artist} - {title} ({album}) [{money}€]\n"
        await message.channel.send(tracks_message)

    if message.content.startswith('!albumtracks'):
        # group tracks by album and sum their money
        user_entry = users_table.get(Query().username == message.author.name)
        if not user_entry:
            await message.channel.send(f"{message.author.name}, set up your account first by setting your Last.fm username.")
            return
        user_tracks = user_entry['user_tracks']
        if not user_tracks:
            await message.channel.send(f"{message.author.name}, you have no recorded tracks yet.")
            return
        album_dict = {}
        for artist, title, album, money in user_tracks:
            if album != "Single":
                if album not in album_dict:
                    album_dict[album] = {
                        'artist': artist,
                        'total_money': 0,
                    }
                album_dict[album]['total_money'] += money
        album_message = "Your recorded albums:\n"
        for album, info in album_dict.items():
            album_message += f"{info['artist']} - {album} [{info['total_money']}€]\n"
        await message.channel.send(album_message)

    if message.content.startswith('!artisttracks'):
        user_entry = users_table.get(Query().username == message.author.name)
        if not user_entry:
            await message.channel.send(f"{message.author.name}, set up your account first by setting your Last.fm username.")
            return
        user_tracks = user_entry['user_tracks']
        if not user_tracks:
            await message.channel.send(f"{message.author.name}, you have no recorded tracks yet.")
            return
        artist_dict = {}
        for artist, title, album, money in user_tracks:
            if artist not in artist_dict:
                artist_dict[artist] = {
                    'total_money': 0,
                }
            artist_dict[artist]['total_money'] += money
        artist_message = "Your recorded artists:\n"
        for artist, info in artist_dict.items():
            artist_message += f"{artist} [{info['total_money']}€]\n"
        await message.channel.send(artist_message)    

client.run(required_env("DISCORD_TOKEN"))