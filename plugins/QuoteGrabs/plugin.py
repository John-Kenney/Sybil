###
# Copyright (c) 2004, Daniel DiPaolo
# Copyright (c) 2008-2010, James Vega
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
###

import os
import time
import random

import supybot.dbi as dbi
import supybot.conf as conf
import supybot.utils as utils
from supybot.commands import *
import supybot.ircmsgs as ircmsgs
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks

class QuoteGrabsRecord(dbi.Record):
    __fields__ = [
        'by',
        'text',
        'grabber',
        'at',
        'hostmask',
        ]

    def __str__(self):
        grabber = plugins.getUserName(self.grabber)
        return format('%s (Said by: %s; grabbed by %s at %t)',
                      self.text, self.hostmask, grabber, self.at)

class SqlAlchemyQuoteGrabsDB(object):
    def __init__(self, filename, connection, listeners):
        self.filename = filename
        self.connection = connection
        self.listeners = listeners
        self.dbs = ircutils.IrcDict()
        self.meta = ircutils.IrcDict()

    def close(self):
        self.dbs.clear()

    def _getDb(self, channel):
        import datetime
        try:
            import sqlalchemy as sql
            self.sql = sql
        except ImportError:
            raise callbacks.Error, \
                    'You need to have SQLAlchemy installed to use this ' \
                    'plugin.  Download it at <http://www.sqlalchemy.org/>'

        if channel in self.dbs:
            return self.dbs[channel]

        class Timestamp(sql.types.TypeDecorator):
            """type that decorates TIMESTAMP to give back seconds since epoch
            value instead of datetime object"""
            impl = sql.TIMESTAMP
            def process_result_value(self, value, dialect):
                if value is not None:
                    value = time.mktime(value.utctimetuple())
                return value

        filename = plugins.makeChannelFilename(self.filename, channel)
        engine = sql.create_engine(self.connection + filename,
                                   listeners=self.listeners)
        metadata = sql.MetaData()
        quotegrabs = sql.Table('quotegrabs', metadata,
                               sql.Column('id', sql.Integer,
                                          primary_key=True, unique=True),
                               sql.Column('nick', sql.Text),
                               sql.Column('hostmask', sql.Text),
                               sql.Column('added_by', sql.Text),
                               sql.Column('added_at', Timestamp,
                                          default=datetime.datetime.now),
                               sql.Column('quote', sql.Text),
                              )
        metadata.create_all(engine)
        self.dbs[channel] = (engine, quotegrabs)
        return self.dbs[channel]

    def get(self, channel, id):
        (db, quotegrabs) = self._getDb(channel)
        s = self.sql.select([quotegrabs], quotegrabs.c.id==id)
        result = db.execute(s)
        quote = result.fetchone()
        result.close()
        if result is None:
            raise dbi.NoRecordError
        return QuoteGrabsRecord(quote['id'], by=quote['nick'],
                                text=quote['quote'],
                                hostmask=quote['hostmask'],
                                at=quote['added_at'],
                                grabber=quote['added_by'])

    def random(self, channel, nick):
        (db, quotegrabs) = self._getDb(channel)
        if nick:
            s = self.sql.select([quotegrabs.c.quote],
                                quotegrabs.c.nick.like(nick)) \
                               .order_by(self.sql.func.random()).limit(1)
        else:
            s = self.sql.select([quotegrabs.c.quote]) \
                          .order_by(self.sql.func.random()) \
                          .limit(1)
        results = db.execute(s)
        quote = results.fetchone()
        results.close()
        if quote is None:
            raise dbi.NoRecordError
        return quote[0]

    def list(self, channel, nick):
        (db, quotegrabs) = self._getDb(channel)
        s = self.sql.select([quotegrabs.c.id, quotegrabs.c.quote],
                            quotegrabs.c.nick.like(nick)) \
                           .order_by(quotegrabs.c.id.desc())
        results = db.execute(s)
        quotes = results.fetchall()
        results.close()
        if not quotes:
            raise dbi.NoRecordError
        return [QuoteGrabsRecord(id, text=quote)
                for (id, quote) in quotes]

    def getQuote(self, channel, nick):
        (db, quotegrabs) = self._getDb(channel)
        s = self.sql.select([quotegrabs.c.quote],
                            quotegrabs.c.nick.like(nick)) \
                           .order_by(quotegrabs.c.id.desc()).limit(1)
        results = db.execute(s)
        quote = results.fetchone()
        results.close()
        if quote is None:
            raise dbi.NoRecordError
        return quote[0]

    def select(self, channel, nick):
        (db, quotegrabs) = self._getDb(channel)
        s = self.sql.select([quotegrabs.c.added_at],
                            quotegrabs.c.nick.like(nick)) \
                           .order_by(quotegrabs.c.id.desc()).limit(1)
        results = db.execute(s)
        r = results.fetchone()
        results.close()
        if r is None:
            raise dbi.NoRecordError
        return r[0]

    def add(self, channel, msg, by):
        (db, quotegrabs) = self._getDb(channel)
        text = ircmsgs.prettyPrint(msg)
        # Check to see if the latest quotegrab is identical
        s = self.sql.select([quotegrabs.c.quote],
                            quotegrabs.c.nick.like(msg.nick)) \
                           .order_by(quotegrabs.c.id.desc()).limit(1)
        results = db.execute(s)
        r = results.fetchone()
        if r is not None and text == r[0]:
            results.close()
            return
        db.execute(quotegrabs.insert(), nick=msg.nick, hostmask=msg.prefix,
                   added_by=by, quote=text).close()

    def remove(self, channel, grab=None):
        (db, quotegrabs) = self._getDb(channel)
        if grab is not None:
            # the testing if there actually *is* the to-be-deleted record is
            # strictly unnecessary -- the DELETE operation would "succeed"
            # anyway, but it's silly to just keep saying 'OK' no matter what,
            # so...
            s = self.sql.select([quotegrabs.c.quote],
                                quotegrabs.c.id == grab)
            results = db.execute(s)
            r = results.fetchone()
            if r is None:
                raise dbi.NoRecordError
            db.execute(quotegrabs.delete(), id = grab).close()
        else:
            maxs = self.sql.select([self.sql.func.max(quotegrabs.c.id) \
                                    .label('maxid')]).alias('maxs')
            s = self.sql.select([quotegrabs.c.id],
                                quotegrabs.c.id == maxs.c.maxid)
            results = db.execute(s)
            r = results.fetchone()
            if r is None:
                raise dbi.NoRecordError
            db.execute(quotegrabs.delete(), id = r[0]).close()

    def search(self, channel, text):
        (db, quotegrabs) = self._getDb(channel)
        s = self.sql.select([quotegrabs.c.id, quotegrabs.c.nick,
                             quotegrabs.c.quote],
                            quotegrabs.c.quote.like('%%%s%%' % text)) \
                           .order_by(quotegrabs.c.id.desc())
        results = db.execute(s)
        r = results.fetchall()
        results.close()
        if not r:
            raise dbi.NoRecordError
        quotes = [QuoteGrabsRecord(id, text=quote, by=nick)
                  for (id, nick, quote) in r]
        return quotes

class SqliteQuoteGrabsDB(object):
    def __init__(self, filename):
        self.dbs = ircutils.IrcDict()
        self.filename = filename

    def close(self):
        for db in self.dbs.itervalues():
            db.close()

    def _getDb(self, channel):
        filename = plugins.makeChannelFilename(self.filename, channel)
        try:
            import sqlite3
        except ImportError:
            from pysqlite2 import dbapi2 as sqlite3 # for python2.4
        def p(s1, s2):
            # text_factory seems to only apply as an output adapter,
            # so doesn't apply to created functions; so we use str()
            return int(ircutils.nickEqual(s1.encode('iso8859-1'),
                                          s2.encode('iso8859-1')))
        if filename in self.dbs:
            self.dbs[filename].create_function('nickeq', 2, p)
            return self.dbs[filename]
        if os.path.exists(filename):
            db = sqlite3.connect(filename)
            db.text_factory = str
            db.create_function('nickeq', 2, p)
            self.dbs[filename] = db
            return db
        db = sqlite3.connect(filename)
        db.text_factory = str
        db.create_function('nickeq', 2, p)
        self.dbs[filename] = db
        cursor = db.cursor()
        cursor.execute("""CREATE TABLE quotegrabs (
                          id INTEGER PRIMARY KEY,
                          nick BLOB,
                          hostmask TEXT,
                          added_by TEXT,
                          added_at TIMESTAMP,
                          quote TEXT
                          );""")
        db.commit()
        return db

    def get(self, channel, id):
        db = self._getDb(channel)
        cursor = db.cursor()
        cursor.execute("""SELECT id, nick, quote, hostmask, added_at, added_by
                          FROM quotegrabs WHERE id = ?""", (id,))
        result = cursor.fetchone()
        if result:
            (id, by, quote, hostmask, at, grabber) = result
            return QuoteGrabsRecord(id, by=by, text=quote, hostmask=hostmask,
                                    at=int(at), grabber=grabber)
        else:
            raise dbi.NoRecordError

    def random(self, channel, nick):
        db = self._getDb(channel)
        cursor = db.cursor()
        if nick:
            cursor.execute("""SELECT quote FROM quotegrabs
                              WHERE nickeq(nick, ?)
                              ORDER BY random() LIMIT 1""",
                              (nick,))
        else:
            cursor.execute("""SELECT quote FROM quotegrabs
                              ORDER BY random() LIMIT 1""")
        result = cursor.fetchone()
        if result:
            return result[0]
        else:
            raise dbi.NoRecordError


    def list(self, channel, nick):
        db = self._getDb(channel)
        cursor = db.cursor()
        cursor.execute("""SELECT id, quote FROM quotegrabs
                          WHERE nickeq(nick, ?)
                          ORDER BY id DESC""", (nick,))
        results = cursor.fetchall()
        if len(results) == 0:
            raise dbi.NoRecordError
        return [QuoteGrabsRecord(id, text=quote)
                for (id, quote) in results]

    def getQuote(self, channel, nick):
        db = self._getDb(channel)
        cursor = db.cursor()
        cursor.execute("""SELECT quote FROM quotegrabs
                          WHERE nickeq(nick, ?)
                          ORDER BY id DESC LIMIT 1""", (nick,))
        quote = cursor.fetchone()
        if quote:
            return quote[0]
        else:
            raise dbi.NoRecordError

    def select(self, channel, nick):
        db = self._getDb(channel)
        cursor = db.cursor()
        cursor.execute("""SELECT added_at FROM quotegrabs
                          WHERE nickeq(nick, ?)
                          ORDER BY id DESC LIMIT 1""", (nick,))
        addedTime = cursor.fetchone()
        if addedTime:
            return addedTime[0]
        else:
            raise dbi.NoRecordError

    def add(self, channel, msg, by):
        db = self._getDb(channel)
        cursor = db.cursor()
        text = ircmsgs.prettyPrint(msg)
        # Check to see if the latest quotegrab is identical
        cursor.execute("""SELECT quote FROM quotegrabs
                          WHERE nick=?
                          ORDER BY id DESC LIMIT 1""", (msg.nick,))
        quote = cursor.fetchone()
        if quote and text == quote[0]:
            return
        cursor.execute("""INSERT INTO quotegrabs
                          VALUES (NULL, ?, ?, ?, ?, ?)""",
                       (msg.nick, msg.prefix, by, int(time.time()), text,))
        db.commit()

    def remove(self, channel, grab=None):
        db = self._getDb(channel)
        cursor = db.cursor()
        if grab is not None:
            # the testing if there actually *is* the to-be-deleted record is
            # strictly unnecessary -- the DELETE operation would "succeed"
            # anyway, but it's silly to just keep saying 'OK' no matter what,
            # so...
            cursor.execute("""SELECT * FROM quotegrabs WHERE id = ?""", (grab,))
            results = cursor.fetchall()
            if len(results) == 0:
                raise dbi.NoRecordError
            cursor.execute("""DELETE FROM quotegrabs WHERE id = ?""", (grab,))
        else:
            cursor.execute("""SELECT * FROM quotegrabs WHERE id = (SELECT MAX(id)
                FROM quotegrabs)""")
            results = cursor.fetchall()
            if len(results) == 0:
                raise dbi.NoRecordError
            cursor.execute("""DELETE FROM quotegrabs WHERE id = (SELECT MAX(id)
                FROM quotegrabs)""")
        db.commit()

    def search(self, channel, text):
        db = self._getDb(channel)
        cursor = db.cursor()
        text = '%' + text + '%'
        cursor.execute("""SELECT id, nick, quote FROM quotegrabs
                          WHERE quote LIKE ?
                          ORDER BY id DESC""", (text,))
        results = cursor.fetchall()
        if len(results) == 0:
            raise dbi.NoRecordError
        return [QuoteGrabsRecord(id, text=quote, by=nick)
                for (id, nick, quote) in results]

QuoteGrabsDB = plugins.DB('QuoteGrabs', {'sqlite3': SqliteQuoteGrabsDB,
                                         'sqlalchemy': SqlAlchemyQuoteGrabsDB})

class QuoteGrabs(callbacks.Plugin):
    """Add the help for "@help QuoteGrabs" here."""
    def __init__(self, irc):
        self.__parent = super(QuoteGrabs, self)
        self.__parent.__init__(irc)
        self.db = QuoteGrabsDB()

    def doPrivmsg(self, irc, msg):
        if ircmsgs.isCtcp(msg) and not ircmsgs.isAction(msg):
            return
        irc = callbacks.SimpleProxy(irc, msg)
        if irc.isChannel(msg.args[0]):
            (chan, payload) = msg.args
            words = self.registryValue('randomGrabber.minimumWords', chan)
            length = self.registryValue('randomGrabber.minimumCharacters',chan)
            grabTime = \
            self.registryValue('randomGrabber.averageTimeBetweenGrabs', chan)
            channel = plugins.getChannel(chan)
            if self.registryValue('randomGrabber', chan):
                if len(payload) > length and len(payload.split()) > words:
                    try:
                        last = int(self.db.select(channel, msg.nick))
                    except dbi.NoRecordError:
                        self._grab(channel, irc, msg, irc.prefix)
                        self._sendGrabMsg(irc, msg)
                    else:
                        elapsed = int(time.time()) - last
                        if (random.random() * elapsed) > (grabTime / 2):
                            self._grab(channel, irc, msg, irc.prefix)
                            self._sendGrabMsg(irc, msg)

    def _grab(self, channel, irc, msg, addedBy):
        self.db.add(channel, msg, addedBy)

    def _sendGrabMsg(self, irc, msg):
        s = 'jots down a new quote for %s' % msg.nick
        irc.reply(s, action=True, prefixNick=False)

    def grab(self, irc, msg, args, channel, nick):
        """[<channel>] <nick>

        Grabs a quote from <channel> by <nick> for the quotegrabs table.
        <channel> is only necessary if the message isn't sent in the channel
        itself.
        """
        # chan is used to make sure we know where to grab the quote from, as
        # opposed to channel which is used to determine which db to store the
        # quote in
        chan = msg.args[0]
        if chan is None:
            raise callbacks.ArgumentError
        if ircutils.nickEqual(nick, msg.nick):
            irc.error('You can\'t quote grab yourself.', Raise=True)
        for m in reversed(irc.state.history):
            if m.command == 'PRIVMSG' and ircutils.nickEqual(m.nick, nick) \
                    and ircutils.strEqual(m.args[0], chan):
                self._grab(channel, irc, m, msg.prefix)
                irc.replySuccess()
                return
        irc.error('I couldn\'t find a proper message to grab.')
    grab = wrap(grab, ['channeldb', 'nick'])

    def ungrab(self, irc, msg, args, channel, grab):
        """[<channel>] <number>

        Removes the grab <number> (the last by default) on <channel>.
        <channel> is only necessary if the message isn't sent in the channel
        itself.
        """
        try:
            self.db.remove(channel, grab)
            irc.replySuccess()
        except dbi.NoRecordError:
            if grab is None:
                irc.error('Nothing to ungrab.')
            else:
                irc.error('Invalid grab number.')
    ungrab = wrap(ungrab, ['channeldb', optional('id')])

    def quote(self, irc, msg, args, channel, nick):
        """[<channel>] <nick>

        Returns <nick>'s latest quote grab in <channel>.  <channel> is only
        necessary if the message isn't sent in the channel itself.
        """
        try:
            irc.reply(self.db.getQuote(channel, nick))
        except dbi.NoRecordError:
            irc.error('I couldn\'t find a matching quotegrab for %s.' % nick,
                      Raise=True)
    quote = wrap(quote, ['channeldb', 'nick'])

    def list(self, irc, msg, args, channel, nick):
        """[<channel>] <nick>

        Returns a list of shortened quotes that have been grabbed for <nick>
        as well as the id of each quote.  These ids can be used to get the
        full quote.  <channel> is only necessary if the message isn't sent in
        the channel itself.
        """
        try:
            records = self.db.list(channel, nick)
            L = []
            for record in records:
                # strip the nick from the quote
                quote = record.text.replace('<%s> ' % nick, '', 1)
                item = utils.str.ellipsisify('#%s: %s' % (record.id, quote),50)
                L.append(item)
            irc.reply(utils.str.commaAndify(L))
        except dbi.NoRecordError:
            irc.error('I couldn\'t find any quotegrabs for %s.' % nick,
                      Raise=True)
    list = wrap(list, ['channeldb', 'nick'])

    def random(self, irc, msg, args, channel, nick):
        """[<channel>] [<nick>]

        Returns a randomly grabbed quote, optionally choosing only from those
        quotes grabbed for <nick>.  <channel> is only necessary if the message
        isn't sent in the channel itself.
        """
        try:
            irc.reply(self.db.random(channel, nick))
        except dbi.NoRecordError:
            if nick:
                irc.error('Couldn\'t get a random quote for that nick.')
            else:
                irc.error('Couldn\'t get a random quote.  Are there any '
                          'grabbed quotes in the database?')
    random = wrap(random, ['channeldb', additional('nick')])

    def get(self, irc, msg, args, channel, id):
        """[<channel>] <id>

        Return the quotegrab with the given <id>.  <channel> is only necessary
        if the message isn't sent in the channel itself.
        """
        try:
            irc.reply(self.db.get(channel, id))
        except dbi.NoRecordError:
            irc.error('No quotegrab for id %s' % utils.str.quoted(id),
                      Raise=True)
    get = wrap(get, ['channeldb', 'id'])

    def search(self, irc, msg, args, channel, text):
        """[<channel>] <text>

        Searches for <text> in a quote.  <channel> is only necessary if the
        message isn't sent in the channel itself.
        """
        try:
            records = self.db.search(channel, text)
            L = []
            for record in records:
                # strip the nick from the quote
                quote = record.text.replace('<%s> ' % record.by, '', 1)
                item = utils.str.ellipsisify('#%s: %s' % (record.id, quote),50)
                L.append(item)
            irc.reply(utils.str.commaAndify(L))
        except dbi.NoRecordError:
            irc.error('No quotegrabs matching %s' % utils.str.quoted(text),
                       Raise=True)
    search = wrap(search, ['channeldb', 'text'])

Class = QuoteGrabs

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
