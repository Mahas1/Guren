import asyncio
import datetime
import json
import re
from copy import deepcopy
import os

import discord
from discord import User
from discord.ext import commands, tasks
from dateutil.relativedelta import relativedelta
from discord.utils import get

from utils.util import Pag
from utils import time_calc, misc_checks

import utils.json_loader
from utils import default, permissions
from dateutil.relativedelta import relativedelta


class MemberID(commands.Converter):
    async def convert(self, ctx, argument):
        try:
            m = await commands.MemberConverter().convert(ctx, argument)
        except commands.BadArgument:
            try:
                return int(argument, base=10)
            except ValueError:
                raise commands.BadArgument(f"{argument} is not a valid member or member ID.") from None
        else:
            return m.id


class ActionReason(commands.Converter):
    async def convert(self, ctx, argument):
        ret = argument

        if len(ret) > 512:
            reason_max = 512 - len(ret) - len(argument)
            raise commands.BadArgument(f'reason is too long ({len(argument)}/{reason_max})')
        return ret
    

class Moderation(commands.Cog):
    """Various Moderation Commands."""
    def __init__(self, bot):
        self.bot = bot
        self.mute_task = self.check_current_mutes.start()

    def cog_unload(self):
        self.mute_task.cancel()

    @tasks.loop(minutes=5)
    async def check_current_mutes(self):
        guild = self.bot.get_guild
        currentTime = datetime.datetime.now()
        mutes = deepcopy(self.bot.muted_users)
        for key, value in mutes.items():
            if value['muteDuration'] is None:
                continue
        
            unmuteTime = value['mutedAt'] + relativedelta(seconds=value['muteDuration'])

            if currentTime >= unmuteTime:
                guild = self.bot.get_guild(value['guildId'])
                member = guild.get_member(value['_id'])

                role = discord.utils.get(guild.roles, name="Muted")
                if role in member.roles:
                    await member.remove_roles(role)
                    print(f"Unmuted: {member.name}")

                await self.bot.mutes.delete(member.id)
                try:
                    self.bot.muted_users.pop(member.id)
                except KeyError:
                    pass
    
    @check_current_mutes.before_loop
    async def before_check_current_mutes(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{self.__class__.__name__} Cog has been loaded.\n-----")

    @commands.command()
    @commands.guild_only()
    @permissions.has_permissions(ban_members=True)
    async def ban(self, ctx, member: MemberID, *, reason: str = None):
        """ Bans a user from the current server. """
        m = ctx.guild.get_member(member)
        if m is not None and await permissions.check_priv(ctx, m):
            return

        try:
            await ctx.guild.ban(discord.Object(id=member), reason=default.responsible(ctx.author, reason))
            await ctx.send(default.actionmessage("banned"))
        except Exception as e:
            await ctx.send(e)

    @commands.command()
    @commands.guild_only()
    @commands.max_concurrency(1, per=commands.BucketType.user)
    @permissions.has_permissions(ban_members=True)
    async def massban(self, ctx, reason: ActionReason, *members: MemberID):
        """ Mass bans multiple members from the server. """
        try:
            for member_id in members:
                await ctx.guild.ban(discord.Object(id=member_id), reason=default.responsible(ctx.author, reason))
            await ctx.send(default.actionmessage("massbanned", mass=True))
        except Exception as e:
            await ctx.send(e)

    @commands.command()
    @commands.guild_only()
    @permissions.has_permissions(ban_members=True)
    async def unban(self, ctx, member: MemberID, *, reason: str = None):
        """ Unbans a user from the current server. """
        try:
            await ctx.guild.unban(discord.Object(id=member), reason=default.responsible(ctx.author, reason))
            await ctx.send(default.actionmessage("unbanned"))
        except Exception as e:
            await ctx.send(e)

    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member, *, reason="No reason"):
        try:
            await member.kick(reason=reason)
        except discord.Forbidden:    
            await ctx.send(f"It looks like i dont have the permission `KICK_MEMBERS` to do this. Please check my permissions and try running the command again.")
        else:
            embed = discord.Embed(title=f"`{ctx.author}` kicked {member}", colour=member.color, timestamp=datetime.datetime.utcnow())
            embed.add_field(name="● Details:", value=f" - Reason: {reason}")
            embed.set_footer(icon_url=f"{ctx.author.avatar_url}", text=f"{ctx.author.top_role.name} ")
            await ctx.send(embed=embed)

    @commands.command(name='mute', description='Mutes the person mentioned. Time period is optional.')
    @commands.has_permissions(manage_roles=True)
    @commands.guild_only()
    async def mute_func(self, ctx, user: discord.Member, time_period=None):
        with open(f'bot_config/guild{ctx.guild.id}.json', 'r') as jsonFile:
            data = json.load(jsonFile)
        mute_role_id = data.get('mute_role')
        mute_role = get(ctx.guild.roles, id=int(mute_role_id))  # get the actual mute role from the role's ID
        await user.add_roles(mute_role)  # add the mute role

        if time_period is not None:
            final_time_text = time_calc.time_suffix(time_period)
            await ctx.send(f'{user.display_name} has been muted for {final_time_text}.')
            await asyncio.sleep(time_calc.get_time(time_period))  # sleep for specified time, then remove the muted role
            await user.remove_roles(mute_role)
            await ctx.send(f'{user.display_name} has been unmuted.')
        else:
            await ctx.send(f'{user.display_name} has been muted.')

    @commands.command(name='hardmute', description='Hard-mutes the person mentioned. '
                                                   'This means all roles are removed until the mute period is over. '
                                                   'Time period is optional.')
    @commands.has_permissions(manage_roles=True)
    @commands.guild_only()
    async def hardmute_func(self, ctx, user: discord.Member, time_period=None):
        has_mute_role = misc_checks.check_muted_role(ctx)
        if not has_mute_role:
            await ctx.send(f'It seems {user.display_name} does not have the mute role.\n'
                           f'If something wrong, an administrator can setup the mute role using the `setup` command.')
            return
        if await misc_checks.is_author(ctx, user):
            return await ctx.send('You cannot mute yourself. Sorry lol')

        if misc_checks.is_client(self.bot, user):
            return await ctx.send('I can\'t mute myself, sorry.')

        with open(f'bot_config/guild{ctx.guild.id}.json', 'r') as jsonFile:
            data = json.load(jsonFile)
        mute_role_id = int(data.get('mute_role'))

        mute_role = get(ctx.guild.roles, id=mute_role_id)
        rolelist = [r.id for r in user.roles if r != ctx.guild.default_role]

        if not os.path.exists(f'bot_config/mute_files/guild{ctx.guild.id}.json'):
            with open(f'bot_config/mute_files/guild{ctx.guild.id}.json') as createFile:
                json.dump({}, createFile)
                print(f'Created file guild{ctx.guild.id}.json in bot_config/mute_files...')  # create file if not present

        with open(f'bot_config/mute_files/guild{ctx.guild.id}.json', 'r') as mute_file:
            data = json.load(mute_file)
            data[user.id] = list(rolelist)
        with open(f'bot_config/mute_files/guild{ctx.guild.id}.json', 'r') as mute_file:
            json.dump(data, mute_file)

        for x in rolelist:
            role = get(ctx.guild.roles, id=int(x))
            try:  # remove every role one by one
                await user.remove_roles(role)
            except:
                await ctx.send(f'Could not remove role {role.name} from {user.display_name}...')
                continue

        await user.add_roles(mute_role)  # add the mute role

        if time_period is not None:
            final_time_text = time_calc.get_time(time_period)
            await ctx.send(f'{user.display_name} has been muted for {final_time_text}.')
            await asyncio.sleep(time_calc.get_time(time_period))
            if mute_role not in user.roles:
                await Moderation.unmute_func(ctx, user)

    @commands.command(name='unmute', description='Unmutes the user mentioned if muted previously.\n'
                                                 'It also attempts to add roles from before they were hard-muted '
                                                 '(if they were).\n'
                                                 'So don\'nt panic if it tries to add roles and fails.')
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    async def unmute_func(self, ctx, user: discord.Member):
        if not os.path.exists(f'bot_config/guild{ctx.guild.id}.json'):
            with open(f'bot_config/guild{ctx.guild.id}.json', 'w') as createFile:
                json.dump({}, createFile)
                print(f'Created file guild{ctx.guild.id}.json in bot_config...')  # create file if not present

        with open(f'bot_config/guild{ctx.guild.id}.json', 'r') as jsonFile:
            data = json.load(jsonFile)
        mute_role_id = int(data.get('mute_role'))

        mute_role = get(ctx.guild.roles, id=mute_role_id)

        with open(f'bot_config/mute_files/guild{ctx.guild.id}.json', 'r') as mute_file:
            data = json.load(mute_file)
        role_ids = data.get(f'{user.id}')
        if role_ids is not None:
            for x in role_ids:
                actual_role = get(ctx.guild.roles, id=int(x))
                try:
                    await user.add_roles(actual_role)
                except:
                    await ctx.send(f'Could not add role **{actual_role.name}** to **{user.display_name}**')
                    continue
        await user.remove_roles(mute_role)
        await ctx.send(f'{user.display_name} has been unmuted.')

        data.pop(user.id)  # since they're unmuted, we don't need the role list

        with open(f'bot_config/mute_files/guild{ctx.guild.id}.json', 'w') as mute_file:
            json.dump(data, mute_file)

    @commands.command(name="clear")
    @commands.has_permissions(manage_messages=True)
    async def clear(self, ctx, amount: int):
        channel = ctx.channel
        try:
            await channel.purge(limit=amount+1)
        except discord.Forbidden:
            await ctx.send(f"It looks like i dont have the permission `MANAGE_MESSAGES` to do this. Please check my permissions and try running the command again.")
        else:
            await ctx.send(f"{amount} messages deleted.")


    @commands.command()
    @commands.guild_only()
    @permissions.has_permissions(ban_members=True)
    async def warn(self, ctx, member: discord.Member, *, reason):
        """warns an user. ID, Mention or name."""
        if member.id in [ctx.author.id, self.bot.user.id]:
            return await ctx.send("You cannot warn yourself or the bot!")
        
        current_warn_count = len(
            await self.bot.warns.find_many_by_custom(
                {
                    "user_id": member.id,
                    "guild_id": member.guild.id
                }
            )
        ) + 1
        
        warn_filter = {"user_id": member.id, "guild_id": member.guild.id, "number": current_warn_count}
        warn_data = {"reason": reason, "timestamp": ctx.message.created_at, "warned_by": ctx.author.id}
        
        await self.bot.warns.upsert_custom(warn_filter, warn_data)
        
        embed = discord.Embed(
            title="You are being warned:",
            description=f"__**Reason**__:\n{reason}",
            colour=discord.Colour.red(),
            timestamp=ctx.message.created_at
        )
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon_url)
        embed.set_footer(text=f"Warn: {current_warn_count}")
        
        try:
            await member.send(embed=embed)
            await ctx.send("Warned that user in dm's")
        except discord.HTTPException:
            await ctx.send(member.mention, embed=embed)
            
    @commands.command()
    @commands.guild_only()
    @permissions.has_permissions(ban_members=True)
    async def warns(self, ctx, member: discord.Member):
        """Shows all warnings for a specified user. ID, Mention or Name"""
        warn_filter = {"user_id": member.id, "guild_id": member.guild.id}
        warns = await self.bot.warns.find_many_by_custom(warn_filter)
        
        if not bool(warns):
            return await ctx.send(f"Couldn't find any warns for: `{member.display_name}`")
        
        warns = sorted(warns, key=lambda x: x["number"])
        
        pages = []
        for warn in warns:
            description = f"""
            Warn Number: `{warn['number']}`
            Warn Reason: `{warn['reason']}`
            Warned By: <@{warn['warned_by']}>
            Warn Number: {warn['timestamp'].strftime("%I:%M %p %B %d, %Y")}
            """
            pages.append(description)
        
        await Pag(
            title=f"Warns for `{member.display_name}`",
            colour=0xCE2029,
            entries=pages,
            length=1
        ).start(ctx)

def setup(bot):
    bot.add_cog(Moderation(bot))
