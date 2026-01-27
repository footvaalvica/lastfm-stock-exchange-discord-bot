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
    user_scrobbles = user_entry['user_scrobbles']

    daily_scrobbles = user.get_recent_tracks(now_playing=False, limit=None, time_from=last_updated)
    users_table.update({'last_updated': timestamp}, Query().username == user.get_name())

    if not daily_scrobbles:
        return user_money_sum
    
    for scrobble in daily_scrobbles:
        print(f"Processing track: {scrobble.track}")
        artist = scrobble.track.artist
        popularity = int(artist.get_listener_count())

        # Check if we have an entry for this artist on this date
        ArtistInfo = Query()
        artist_popularity_entry = artist_popularity_table.search((ArtistInfo.artist_name == artist.name) & (ArtistInfo.timestamp == timestamp))
        
        # if we don't have an entry for this artist on this date, create one
        if not artist_popularity_entry:
            artist_popularity_table.insert({
                'artist_name': artist.name,
                'popularity': popularity,
                'timestamp': timestamp
            })
            
            # get all entries for this artist from all users
            all_user_entries = users_table.search(Query().username.exists())
            for user_entry in all_user_entries:
                user_scrobbles = user_entry['user_scrobbles']
                updated_scrobbles = []
                user_money_sum = user_entry['money']
                for scrobble_info in user_scrobbles:
                    scrobble_artist, scrobble_title, scrobble_album, _, scrobble_timestamp, original_scrobble_popularity = scrobble_info
                    if scrobble_artist == artist.name:
                        new_popularity_difference = popularity - original_scrobble_popularity + 1
                        user_money_sum += new_popularity_difference
                        updated_scrobbles.append((scrobble_artist, scrobble_title, scrobble_album, new_popularity_difference, scrobble_timestamp, original_scrobble_popularity))
                    else:
                        updated_scrobbles.append(scrobble_info)
                users_table.update({'user_scrobbles': updated_scrobbles, 'money': user_money_sum}, Query().username == user_entry['username'])
            print(f"Updated all previous scrobbles for artist {artist.name} for all users.")
            
        scrobble_timestamp = datetime.fromtimestamp(int(scrobble.timestamp)).date().isoformat().replace('-', '')
        print(f"Track timestamp: {scrobble_timestamp}, Last updated: {last_updated}, Current timestamp: {timestamp}")

        # get the closest matching entry for this track timestamp
        artist_popularity_entries = artist_popularity_table.search(ArtistInfo.artist_name == artist.name)
        artist_popularity_entry = min(artist_popularity_entries, key=lambda x: abs(int(x['timestamp']) - int(scrobble_timestamp)))
        popularity_difference = popularity - artist_popularity_entry['popularity'] + 1
        user_money_sum += popularity_difference
        users_table.update({'money': user_money_sum}, Query().username == user.get_name())

        if scrobble.track.get_album() is not None:
            album_title = scrobble.track.get_album().title
        else:
            album_title = "Single"
        user_scrobbles.append((scrobble.track.artist.name, scrobble.track.title, album_title, popularity_difference, scrobble_timestamp, popularity))
    
    users_table.update({'user_scrobbles': user_scrobbles}, Query().username == user.get_name())
    
    print(artist_popularity_table.all())
    print(users_table.all())
    return user_money_sum

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
        print(f"Fetching money for user: {message.author.name}")
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
                'user_scrobbles': []
            })
            await message.channel.send(f"{message.author.name}, your Last.fm username has been set to {lastfm_username}.")
        except IndexError:
            await message.channel.send("Please provide a Last.fm username.")

    if message.content.startswith('!scrobbles'):
        user_entry = users_table.get(Query().username == message.author.name)
        if not user_entry:
            await message.channel.send(f"{message.author.name}, set up your account first by setting your Last.fm username.")
            return
        user_scrobbles = user_entry['user_scrobbles']
        if not user_scrobbles:
            await message.channel.send(f"{message.author.name}, you have no recorded scrobbles yet.")
            return
        scrobbles_message = "Your recorded scrobbles:\n"
        for artist, title, album, money in user_scrobbles:
                scrobbles_message += f"{artist} - {title} ({album}) [{money}€]\n"
        await message.channel.send(scrobbles_message)

    if message.content.startswith('!albumscrobbles'):
        # group scrobbles by album and sum their money
        user_entry = users_table.get(Query().username == message.author.name)
        if not user_entry:
            await message.channel.send(f"{message.author.name}, set up your account first by setting your Last.fm username.")
            return
        user_scrobbles = user_entry['user_scrobbles']
        if not user_scrobbles:
            await message.channel.send(f"{message.author.name}, you have no recorded scrobbles yet.")
            return
        album_dict = {}
        for artist, title, album, money in user_scrobbles:
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

    if message.content.startswith('!artistscrobbles'):
        user_entry = users_table.get(Query().username == message.author.name)
        if not user_entry:
            await message.channel.send(f"{message.author.name}, set up your account first by setting your Last.fm username.")
            return
        user_scrobbles = user_entry['user_scrobbles']
        if not user_scrobbles:
            await message.channel.send(f"{message.author.name}, you have no recorded scrobbles yet.")
            return
        artist_dict = {}
        for artist, title, album, money in user_scrobbles:
            if artist not in artist_dict:
                artist_dict[artist] = {
                    'total_money': 0,
                }
            artist_dict[artist]['total_money'] += money
        artist_message = "Your recorded artists:\n"
        for artist, info in artist_dict.items():
            artist_message += f"{artist} [{info['total_money']}€]\n"
        await message.channel.send(artist_message)

    if message.content.startswith('!help'):
        help_message = (
            "Available commands:\n"
            "!setlastfm <username> - Set your Last.fm username\n"
            "!money - Check your total money\n"
            "!leaderboard - View the leaderboard\n"
            "!scrobbles - View your recorded scrobbles\n"
            "!albumscrobbles - View your recorded albums and their total money\n"
            "!artistscrobbles - View your recorded artists and their total money\n"
            "!help - Show this help message"
        )
        await message.channel.send(help_message)

client.run(required_env("DISCORD_TOKEN"))