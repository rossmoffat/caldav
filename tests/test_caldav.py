#!/usr/bin/env python
# -*- encoding: utf-8 -*-

import logging
import threading
import time
import vobject
import uuid
import tempfile
from datetime import datetime
from six import PY3
from nose.tools import assert_equal, assert_not_equal, assert_raises
from nose.plugins.skip import SkipTest
from requests.packages import urllib3

from .conf import caldav_servers, proxy, proxy_noport, test_xandikos, xandikos_port, xandikos_host
from .proxy import ProxyHandler, NonThreadingHTTPServer

from caldav.davclient import DAVClient
from caldav.objects import (Principal, Calendar, Event, DAVObject,
                            CalendarSet, FreeBusy)
from caldav.lib.url import URL
from caldav.lib import url
from caldav.lib import error
from caldav.elements import dav, cdav
from caldav.lib.python_utilities import to_local, to_str

if test_xandikos:
    from xandikos.web import XandikosBackend, XandikosApp
    from wsgiref.simple_server import make_server

if PY3:
    from urllib.parse import urlparse
else:
    from urlparse import urlparse

log = logging.getLogger("caldav")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ev1 = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example Corp.//CalDAV Client//EN
BEGIN:VEVENT
UID:20010712T182145Z-123401@example.com
DTSTAMP:20060712T182145Z
DTSTART:20060714T170000Z
DTEND:20060715T040000Z
SUMMARY:Bastille Day Party
END:VEVENT
END:VCALENDAR
"""

ev2 = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example Corp.//CalDAV Client//EN
BEGIN:VEVENT
UID:20010712T182145Z-123401@example.com
DTSTAMP:20070712T182145Z
DTSTART:20070714T170000Z
DTEND:20070715T040000Z
SUMMARY:Bastille Day Party +1year
END:VEVENT
END:VCALENDAR
"""

# example from http://www.rfc-editor.org/rfc/rfc5545.txt
evr = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example Corp.//CalDAV Client//EN
BEGIN:VEVENT
UID:19970901T130000Z-123403@example.com
DTSTAMP:19970901T130000Z
DTSTART;VALUE=DATE:19971102
SUMMARY:Our Blissful Anniversary
TRANSP:TRANSPARENT
CLASS:CONFIDENTIAL
CATEGORIES:ANNIVERSARY,PERSONAL,SPECIAL OCCASION
RRULE:FREQ=YEARLY
END:VEVENT
END:VCALENDAR"""

# example from http://www.rfc-editor.org/rfc/rfc5545.txt
todo = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example Corp.//CalDAV Client//EN
BEGIN:VTODO
UID:20070313T123432Z-456553@example.com
DTSTAMP:20070313T123432Z
DUE;VALUE=DATE:20070501
SUMMARY:Submit Quebec Income Tax Return for 2006
CLASS:CONFIDENTIAL
CATEGORIES:FAMILY,FINANCE
STATUS:NEEDS-ACTION
END:VTODO
END:VCALENDAR"""

# example from RFC2445, 4.6.2
todo2 = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example Corp.//CalDAV Client//EN
BEGIN:VTODO
UID:19970901T130000Z-123404@host.com
DTSTAMP:19970901T130000Z
DTSTART:19970415T133000Z
DUE:19970416T045959Z
SUMMARY:1996 Income Tax Preparation
CLASS:CONFIDENTIAL
CATEGORIES:FAMILY,FINANCE
PRIORITY:2
STATUS:NEEDS-ACTION
END:VTODO
END:VCALENDAR"""

todo3 = """
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example Corp.//CalDAV Client//EN
BEGIN:VTODO
UID:19970901T130000Z-123405@host.com
DTSTAMP:19970901T130000Z
DTSTART:19970415T133000Z
DUE:19970516T045959Z
SUMMARY:1996 Income Tax Preparation
CLASS:CONFIDENTIAL
CATEGORIES:FAMILY,FINANCE
PRIORITY:1
END:VTODO
END:VCALENDAR"""

todo4 = """
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example Corp.//CalDAV Client//EN
BEGIN:VTODO
UID:19970901T130000Z-123406@host.com
DTSTAMP:19970901T130000Z
SUMMARY:1996 Income Tax Preparation
CLASS:CONFIDENTIAL
CATEGORIES:FAMILY,FINANCE
PRIORITY:1
STATUS:NEEDS-ACTION
END:VTODO
END:VCALENDAR"""

# example from http://www.kanzaki.com/docs/ical/vjournal.html
journal = """
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example Corp.//CalDAV Client//EN
BEGIN:VJOURNAL
UID:19970901T130000Z-123405@example.com
DTSTAMP:19970901T130000Z
DTSTART;VALUE=DATE:19970317
SUMMARY:Staff meeting minutes
DESCRIPTION:1. Staff meeting: Participants include Joe\, Lisa
  and Bob. Aurora project plans were reviewed. There is currently
  no budget reserves for this project. Lisa will escalate to
  management. Next meeting on Tuesday.\n
END:VJOURNAL
END:VCALENDAR
"""

class RepeatedFunctionalTestsBaseClass(object):
    """This is a class with functional tests (tests that goes through
    basic functionality and actively communicates with third parties)
    that we want to repeat for all configured caldav_servers.

    (what a truely ugly name for this class - any better ideas?)

    NOTE: this tests relies heavily on the assumption that we can create
    calendars on the remote caldav server, but the RFC says ...

       Support for MKCALENDAR on the server is only RECOMMENDED and not
       REQUIRED because some calendar stores only support one calendar per
       user (or principal), and those are typically pre-created for each
       account.

    On iCloud I've been denied creating a calendar.  Creating a
    calendar through the WebUI works, and creating an event through
    the library fails, so I don't think the problem is lack of
    MKCALENDAR support.

    On Radicale, apparently there is some bug with MKCALENDAR, ref
    https://github.com/Kozea/Radicale/issues/330
    """
    def setup(self):
        logging.debug("############## test setup")

        if self.server_params.get('unique_calendar_ids', False):
            self.testcal_id = 'testcalendar-' + str(uuid.uuid4())
            self.testcal_id2 = 'testcalendar-' + str(uuid.uuid4())
        else:
            self.testcal_id = "pythoncaldav-test"
            self.testcal_id2 = "pythoncaldav-test2"

        self.conn_params = self.server_params.copy()
        for x in list(self.conn_params.keys()):
            if x not in ('url', 'proxy', 'username', 'password',
                         'ssl_verify_cert'):
                self.conn_params.pop(x)
        self.caldav = DAVClient(**self.conn_params)
        self.principal = self.caldav.principal()

        logging.debug("## going to tear down old test calendars, "
                      "in case teardown wasn't properly executed "
                      "last time tests were run")
        self._teardown()

        logging.debug("##############################")
        logging.debug("############## test setup done")
        logging.debug("##############################")

    def teardown(self):
        logging.debug("############################")
        logging.debug("############## test teardown")
        logging.debug("############################")
        self._teardown()
        logging.debug("############## test teardown done")

    def _teardown(self):
        for combos in (('Yep', self.testcal_id),
                       ('Yep', self.testcal_id2),
                       ('Yølp', self.testcal_id),
                       ('Yep', 'Yep'),
                       ('Yølp', 'Yølp')):
            try:
                cal = self.principal.calendar(name="Yep",
                                              cal_id=self.testcal_id)
                cal.delete()
            except:
                pass

    def testPropfind(self):
        """
        Test of the propfind methods. (This is sort of redundant, since
        this is implicitly run by the setup)
        """
        # ResourceType MUST be defined, and SHOULD be returned on a propfind
        # for "allprop" if I have the permission to see it.
        # So, no ResourceType returned seems like a bug in bedework
        if 'nopropfind' in self.server_params:
            raise SkipTest("Skipping propfind test, "
                           "re test suite configuration.  "
                           "Perhaps the caldav server is not adhering to "
                           "the standards")

        # first a raw xml propfind to the root URL
        foo = self.caldav.propfind(
            self.principal.url,
            props='<?xml version="1.0" encoding="UTF-8"?>'
                  '<D:propfind xmlns:D="DAV:">'
                  '  <D:allprop/>'
                  '</D:propfind>')
        assert('resourcetype' in to_local(foo.raw))

        # next, the internal _query_properties, returning an xml tree ...
        foo2 = self.principal._query_properties([dav.Status(), ])
        assert('resourcetype' in to_local(foo.raw))
        # TODO: more advanced asserts

    def testGetCalendarHomeSet(self):
        chs = self.principal.get_properties([cdav.CalendarHomeSet()])
        assert '{urn:ietf:params:xml:ns:caldav}calendar-home-set' in chs

    def testGetDefaultCalendar(self):
        if 'nodefaultcalendar' in self.server_params:
            raise SkipTest("Skipping GetDefaultCalendar, caldav server has no default calendar for the user?")
        assert_not_equal(len(self.principal.calendars()), 0)
        
    def testGetCalendar(self):
        # Create calendar
        c = self.principal.make_calendar(name="Yep", cal_id=self.testcal_id)
        assert_not_equal(c.url, None)
        assert_not_equal(len(self.principal.calendars()), 0)

    def testProxy(self):
        if self.caldav.url.scheme == 'https' or 'noproxy' in self.server_params:
            raise SkipTest("Skipping %s.testProxy as the TinyHTTPProxy "
                           "implementation doesn't support https")

        server_address = ('127.0.0.1', 8080)
        proxy_httpd = NonThreadingHTTPServer(
            server_address, ProxyHandler, logging.getLogger("TinyHTTPProxy"))

        threadobj = threading.Thread(target=proxy_httpd.serve_forever)
        try:
            threadobj.start()
            assert(threadobj.is_alive())
            conn_params = self.conn_params.copy()
            conn_params['proxy'] = proxy
            c = DAVClient(**conn_params)
            p = c.principal()
            assert_not_equal(len(p.calendars()), 0)
        finally:
            proxy_httpd.shutdown()
            # this should not be necessary, but I've observed some failures
            if threadobj.is_alive():
                time.sleep(0.05)
            assert(not threadobj.is_alive())

        threadobj = threading.Thread(target=proxy_httpd.serve_forever)
        try:
            threadobj.start()
            assert(threadobj.is_alive())
            conn_params = self.conn_params.copy()
            conn_params['proxy'] = proxy_noport
            c = DAVClient(**conn_params)
            p = c.principal()
            assert_not_equal(len(p.calendars()), 0)
            assert(threadobj.is_alive())
        finally:
            proxy_httpd.shutdown()
            # this should not be necessary
            if threadobj.is_alive():
                time.sleep(0.05)
            assert(not threadobj.is_alive())

    def testPrincipal(self):
        collections = self.principal.calendars()
        if 'principal_url' in self.server_params:
            assert_equal(self.principal.url,
                         self.server_params['principal_url'])
        for c in collections:
            assert_equal(c.__class__.__name__, "Calendar")

    def testCreateDeleteCalendar(self):
        c = self.principal.make_calendar(name="Yep", cal_id=self.testcal_id)
        assert_not_equal(c.url, None)
        events = c.events()
        assert_equal(len(events), 0)
        events = self.principal.calendar(
            name="Yep", cal_id=self.testcal_id).events()
        # huh ... we're quite constantly getting out a list with one item,
        # the URL for the caldav server.  This needs to be investigated,
        # it is surely a bug in our code.
        # Anyway, better to ignore it now than to have broken test code.
        assert_equal(len(events), 0)
        c.delete()

        # verify that calendar does not exist - this breaks with zimbra :-(
        # (also breaks with radicale, which by default creates a new calendar)
        # COMPATIBILITY PROBLEM - todo, look more into it
        if 'nocalendarnotfound' not in self.server_params:
            assert_raises(
                error.NotFoundError,
                self.principal.calendar(
                    name="Yep", cal_id=self.testcal_id).events)

    def testCreateCalendarAndEvent(self):
        c = self.principal.make_calendar(name="Yep", cal_id=self.testcal_id)

        # add event
        c.add_event(ev1)

        # c.events() should give a full list of events
        events = c.events()
        assert_equal(len(events), 1)

        # We should be able to access the calender through the URL
        c2 = Calendar(client=self.caldav, url=c.url)
        events2 = c2.events()
        assert_equal(len(events2), 1)
        assert_equal(events2[0].url, events[0].url)

    def testCreateCalendarAndEventFromVobject(self):
        c = self.principal.make_calendar(name="Yep", cal_id=self.testcal_id)

        # add event from vobject data
        ve1 = vobject.readOne(ev1)
        c.add_event(ve1)

        # c.events() should give a full list of events
        events = c.events()
        assert_equal(len(events), 1)

        # We should be able to access the calender through the URL
        c2 = Calendar(client=self.caldav, url=c.url)
        events2 = c2.events()
        assert_equal(len(events2), 1)
        assert_equal(events2[0].url, events[0].url)

    def testCreateJournalListAndJournalEntry(self):
        """
        This test demonstrates the support for journals.
        * It will create a journal list
        * It will add some journal entries to it
        * It will list out all journal entries
        """
        if 'nojournal' in self.server_params:
            # COMPATIBILITY TODO: read the RFC.  sabredav/owncloud:
            # got the error: "This calendar only supports VEVENT,
            # VTODO. We found a VJOURNAL".  Should probably learn
            # that some other way.  (why doesn't make_calendar break?
            # what does the RFC say on that?)  Same with zimbra,
            # though different error.
            raise SkipTest("Journal testing skipped due to test configuration")
        c = self.principal.make_calendar(
            name="Yep", cal_id=self.testcal_id,
            supported_calendar_component_set=['VJOURNAL'])
        j1 = c.add_journal(journal)
        journals = c.journals()
        assert_equal(len(journals), 1)
        todos = c.todos()
        events = c.events()
        assert_equal(todos + events, [])

    def testCreateTaskListAndTodo(self):
        """
        This test demonstrates the support for task lists.
        * It will create a "task list"
        * It will add a task to it
        * Verify the cal.todos() method
        * Verify that cal.events() method returns nothing
        """
        # bedeworks does not support VTODO
        if 'notodo' in self.server_params:
            raise SkipTest("VTODO testing skipped due to test configuration")

        # For all servers I've tested against except Zimbra, it's
        # possible to create a calendar and add todo-items to it.
        # Zimbra has separate calendars and task lists, and it's not
        # allowed to put TODO-tasks into the calendar.  We need to
        # tell Zimbra that the new "calendar" is a task list.  This
        # is done though the supported_calendar_compontent_set
        # property - hence the extra parameter here:
        logging.info("Creating calendar Yep for tasks")
        c = self.principal.make_calendar(
            name="Yep", cal_id=self.testcal_id,
            supported_calendar_component_set=['VTODO'])

        # add todo-item
        logging.info("Adding todo item to calendar Yep")
        t1 = c.add_todo(todo)

        # c.todos() should give a full list of todo items
        logging.info("Fetching the full list of todo items (should be one)")
        todos = c.todos()
        todos2 = c.todos(include_completed=True)
        assert_equal(len(todos), 1)
        assert_equal(len(todos2), 1)

        logging.info("Fetching the events (should be none)")
        # c.events() should NOT return todo-items
        events = c.events()
        assert_equal(len(events), 0)

    def testTodos(self):
        """
        This test will excercise the cal.todos() method,
        and in particular the sort_keys attribute.
        * It will list out all pending tasks, sorted by due date
        * It will list out all pending tasks, sorted by priority
        """
        # bedeworks does not support VTODO
        if 'notodo' in self.server_params:
            raise SkipTest("VTODO testing skipped due to test configuration")
        c = self.principal.make_calendar(
            name="Yep", cal_id=self.testcal_id,
            supported_calendar_component_set=['VTODO'])

        # add todo-item
        t1 = c.add_todo(todo)
        t2 = c.add_todo(todo2)
        t3 = c.add_todo(todo3)

        todos = c.todos()
        assert_equal(len(todos), 3)

        def uids(lst):
            return [x.instance.vtodo.uid for x in lst]
        assert_equal(uids(todos), uids([t2, t3, t1]))

        todos = c.todos(sort_keys=('priority',))

        def pri(lst):
            return [x.instance.vtodo.priority.value for x in lst
                    if hasattr(x.instance.vtodo, 'priority')]
        assert_equal(pri(todos), pri([t3, t2]))

        todos = c.todos(sort_keys=('summary', 'priority',))
        assert_equal(uids(todos), uids([t3, t2, t1]))

    def testTodoDatesearch(self):
        """
        Let's see how the date search method works for todo events
        """
        # bedeworks does not support VTODO
        if 'notodo' in self.server_params:
            raise SkipTest("VTODO testing skipped due to test configuration")
        c = self.principal.make_calendar(
            name="Yep", cal_id=self.testcal_id,
            supported_calendar_component_set=['VTODO'])

        # add todo-item
        t1 = c.add_todo(todo)
        t2 = c.add_todo(todo2)
        t3 = c.add_todo(todo3)
        t4 = c.add_todo(todo4)
        todos = c.todos()
        assert_equal(len(todos), 4)

        notodos = c.date_search(  # default compfilter is events
            start=datetime(1997, 4, 14), end=datetime(2015, 5, 14),
            expand=False)
        assert(not notodos)

        # Now, this is interesting.  2 events have dtstart set, 3 has
        # due set and 1 has neither due nor dtstart set.  None has
        # duration set.  What will a date search yield?
        todos = c.date_search(
            start=datetime(1997, 4, 14), end=datetime(2015, 5, 14),
            compfilter='VTODO', expand=False)
        # The RFCs are pretty clear on this.  rfc5545 states:

        # A "VTODO" calendar component without the "DTSTART" and "DUE" (or
        # "DURATION") properties specifies a to-do that will be associated
        # with each successive calendar date, until it is completed.

        # and RFC4791, section 9.9 also says that events without
        # dtstart or due should be counted.  Since we have "expand"
        # set, it could even imply that we should get two VTODO items
        # our for each day in the time range!  In any case, less than
        # 4 todos returned is a breach of the RFCs.

        # TODO: prod the caldav server implementators about the RFC
        # breakages.

        # This is probably correct, and most server implementations
        # give this:
        # assert_equal(len(todos), 4)
        # ... but some caldav implementations yields 2 and 3:
        assert(len(todos) >= 2)

    def testTodoCompletion(self):
        """
        Will check that todo-items can be completed and deleted
        """
        # bedeworks does not support VTODO
        if 'notodo' in self.server_params:
            raise SkipTest("VTODO testing skipped due to test configuration")
        c = self.principal.make_calendar(
            name="Yep", cal_id=self.testcal_id,
            supported_calendar_component_set=['VTODO'])

        # add todo-items
        t1 = c.add_todo(todo)
        t2 = c.add_todo(todo2)
        t3 = c.add_todo(todo3)

        # There are now three todo-items at the calendar
        todos = c.todos()
        assert_equal(len(todos), 3)

        # Complete one of them
        t3.complete()

        # There are now two todo-items at the calendar
        todos = c.todos()
        assert_equal(len(todos), 2)

        # The historic todo-item can still be accessed
        todos = c.todos(include_completed=True)
        assert_equal(len(todos), 3)

        t2.delete()

        # ... the deleted one is gone ...
        todos = c.todos(include_completed=True)
        assert_equal(len(todos), 2)

        # date search should not include completed events ... hum.
        # TODO, fixme.
        # todos = c.date_search(
        #     start=datetime(1990, 4, 14), end=datetime(2015,5,14),
        #     compfilter='VTODO', hide_completed_todos=True)
        # assert_equal(len(todos), 1)

    def testUtf8Event(self):
        c = self.principal.make_calendar(name="Yølp", cal_id=self.testcal_id)

        # add event
        e1 = c.add_event(
            ev1.replace("Bastille Day Party", "Bringebærsyltetøyfestival"))

        events = c.events()
        todos = c.todos()

        assert_equal(len(todos), 0)

        # COMPATIBILITY PROBLEM - todo, look more into it
        if 'zimbra' not in str(c.url):
            assert_equal(len(events), 1)

    def testUnicodeEvent(self):
        c = self.principal.make_calendar(name="Yølp", cal_id=self.testcal_id)

        # add event
        e1 = c.add_event(to_str(
            ev1.replace("Bastille Day Party", "Bringebærsyltetøyfestival")))

        # c.events() should give a full list of events
        events = c.events()

        # COMPATIBILITY PROBLEM - todo, look more into it
        if 'zimbra' not in str(c.url):
            assert_equal(len(events), 1)

    def testSetCalendarProperties(self):
        c = self.principal.make_calendar(name="Yep", cal_id=self.testcal_id)
        assert_not_equal(c.url, None)

        props = c.get_properties([dav.DisplayName(), ])
        assert_equal("Yep", props[dav.DisplayName.tag])

        # Creating a new calendar with different ID but with existing name
        # - fails on zimbra only.
        # This is OK to fail.
        if 'zimbra' in str(c.url):
            assert_raises(Exception, self.principal.make_calendar,
                          "Yep", self.testcal_id2)
        else:
            # This may fail, and if it fails, add an exception to the test
            # (see the "if" above)
            cc = self.principal.make_calendar("Yep", self.testcal_id2)
            cc.delete()

        c.set_properties([dav.DisplayName("hooray"), ])
        props = c.get_properties([dav.DisplayName(), ])
        assert_equal(props[dav.DisplayName.tag], "hooray")

        # Creating a new calendar with different ID and old name, this should
        # work, shouldn't it?
        # ... ouch, now it fails with a 409 on zimbra (it didn't fail
        # earlier)
        if not 'zimbra' in str(c.url):
            cc = self.principal.make_calendar(
                name="Yep", cal_id=self.testcal_id2).save()
            assert_not_equal(cc.url, None)
            cc.delete()

    def testLookupEvent(self):
        """
        Makes sure we can add events and look them up by URL and ID
        """
        # Create calendar
        c = self.principal.make_calendar(name="Yep", cal_id=self.testcal_id)
        assert_not_equal(c.url, None)

        # add event
        e1 = c.add_event(ev1)
        assert_not_equal(e1.url, None)

        # Verify that we can look it up, both by URL and by ID
        e2 = c.event_by_url(e1.url)
        e3 = c.event_by_uid("20010712T182145Z-123401@example.com")
        assert_equal(e2.instance.vevent.uid, e1.instance.vevent.uid)
        assert_equal(e3.instance.vevent.uid, e1.instance.vevent.uid)

        # Knowing the URL of an event, we should be able to get to it
        # without going through a calendar object
        e4 = Event(client=self.caldav, url=e1.url)
        e4.load()
        assert_equal(e4.instance.vevent.uid, e1.instance.vevent.uid)

        assert_raises(error.NotFoundError, c.event_by_uid, "0")
        c.add_event(evr)
        assert_raises(error.NotFoundError, c.event_by_uid, "0")

    def testDeleteEvent(self):
        """
        Makes sure we can add events and delete them
        """
        # Create calendar
        c = self.principal.make_calendar(name="Yep", cal_id=self.testcal_id)
        assert_not_equal(c.url, None)

        # add event
        e1 = c.add_event(ev1)
        assert_not_equal(e1.url, None)

        # delete event
        e1.delete()

        # Verify that we can't look it up, both by URL and by ID
        assert_raises(error.NotFoundError, c.event_by_url, e1.url)
        assert_raises(
            error.NotFoundError, c.event_by_uid,
            "20010712T182145Z-123401@example.com")

    def testDateSearchAndFreeBusy(self):
        """
        Verifies that date search works with a non-recurring event
        Also verifies that it's possible to change a date of a
        non-recurring event
        """
        # Create calendar, add event ...
        c = self.principal.make_calendar(name="Yep", cal_id=self.testcal_id)
        assert_not_equal(c.url, None)

        e = c.add_event(ev1)

        # .. and search for it.
        r = c.date_search(datetime(2006, 7, 13, 17, 00, 00),
                          datetime(2006, 7, 15, 17, 00, 00), expand=False)

        assert_equal(e.instance.vevent.uid, r[0].instance.vevent.uid)
        assert_equal(len(r), 1)

        # ev2 is same UID, but one year ahead.
        # The timestamp should change.
        e.data = ev2
        e.save()
        r = c.date_search(datetime(2006, 7, 13, 17, 00, 00),
                          datetime(2006, 7, 15, 17, 00, 00), expand=False)
        assert_equal(len(r), 0)

        r = c.date_search(datetime(2007, 7, 13, 17, 00, 00),
                          datetime(2007, 7, 15, 17, 00, 00), expand=False)
        assert_equal(len(r), 1)

        # date search without closing date should also find it
        r = c.date_search(datetime(2007, 7, 13, 17, 00, 00), expand=False)
        assert_equal(len(r), 1)

        # Lets try a freebusy request as well
        if 'nofreebusy' in self.server_params:
            raise SkipTest("FreeBusy test skipped - not supported by server?")
        freebusy = c.freebusy_request(datetime(2007, 7, 13, 17, 00, 00),
                                      datetime(2007, 7, 15, 17, 00, 00))
        # TODO: assert something more complex on the return object
        assert(isinstance(freebusy, FreeBusy))
        assert(freebusy.instance.vfreebusy)

    def testRecurringDateSearch(self):
        """
        This is more sanity testing of the server side than testing of the
        library per se.  How will it behave if we serve it a recurring
        event?
        """
        if 'norecurring' in self.server_params:
            raise SkipTest("recurring date search test skipped due to "
                           "test configuration")
        c = self.principal.make_calendar(name="Yep", cal_id=self.testcal_id)

        # evr is a yearly event starting at 1997-02-11
        e = c.add_event(evr)
        r = c.date_search(datetime(2008, 11, 1, 17, 00, 00),
                          datetime(2008, 11, 3, 17, 00, 00), expand=True)
        assert_equal(len(r), 1)
        assert_equal(r[0].data.count("END:VEVENT"), 1)
        r = c.date_search(datetime(2008, 11, 1, 17, 00, 00),
                          datetime(2009, 11, 3, 17, 00, 00), expand=True)
        assert_equal(len(r), 1)

        # So much for standards ... seems like different servers
        # behaves differently
        # COMPATIBILITY PROBLEMS - look into it
        if "RRULE" in r[0].data and "BEGIN:STANDARD" not in r[0].data:
            assert_equal(r[0].data.count("END:VEVENT"), 1)
        else:
            assert_equal(r[0].data.count("END:VEVENT"), 2)

        # The recurring events should not be expanded when using the
        # events() method
        r = c.events()
        assert_equal(len(r), 1)

    def testBackwardCompatibility(self):
        """
        Tobias Brox has done some API changes - but this thing should
        still be backward compatible.
        """
        if 'backwards_compatibility_url' not in self.server_params:
            return
        caldav = DAVClient(self.server_params['backwards_compatibility_url'])
        principal = Principal(
            caldav, self.server_params['backwards_compatibility_url'])
        c = Calendar(
            caldav, name="Yep", parent=principal,
            id=self.testcal_id).save()
        assert_not_equal(c.url, None)

        c.set_properties([dav.DisplayName("hooray"), ])
        props = c.get_properties([dav.DisplayName(), ])
        assert_equal(props[dav.DisplayName.tag], "hooray")

        cc = Calendar(caldav, name="Yep", parent=principal).save()
        assert_not_equal(cc.url, None)
        cc.delete()

        e = Event(caldav, data=ev1, parent=c).save()
        assert_not_equal(e.url, None)

        ee = Event(caldav, url=url.make(e.url), parent=c)
        ee.load()
        assert_equal(e.instance.vevent.uid, ee.instance.vevent.uid)

        r = c.date_search(datetime(2006, 7, 13, 17, 00, 00),
                          datetime(2006, 7, 15, 17, 00, 00), expand=False)
        assert_equal(e.instance.vevent.uid, r[0].instance.vevent.uid)
        assert_equal(len(r), 1)

        all = c.events()
        assert_equal(len(all), 1)

        e2 = Event(caldav, data=ev2, parent=c).save()
        assert_not_equal(e.url, None)

        tmp = c.event("20010712T182145Z-123401@example.com")
        assert_equal(e2.instance.vevent.uid, tmp.instance.vevent.uid)

        r = c.date_search(datetime(2007, 7, 13, 17, 00, 00),
                          datetime(2007, 7, 15, 17, 00, 00), expand=False)
        assert_equal(len(r), 1)

        e.data = ev2
        e.save()

        r = c.date_search(datetime(2007, 7, 13, 17, 00, 00),
                          datetime(2007, 7, 15, 17, 00, 00), expand=False)
        # for e in r: print(e.data)
        assert_equal(len(r), 1)

        e.instance = e2.instance
        e.save()
        r = c.date_search(datetime(2007, 7, 13, 17, 00, 00),
                          datetime(2007, 7, 15, 17, 00, 00), expand=False)
        # for e in r: print(e.data)
        assert_equal(len(r), 1)

    def testObjects(self):
        # TODO: description ... what are we trying to test for here?
        o = DAVObject(self.caldav)
        assert_raises(Exception, o.save)

# We want to run all tests in the above class through all caldav_servers;
# and I don't really want to create a custom nose test loader.  The
# solution here seems to be to generate one child class for each
# caldav_url, and inject it into the module namespace. TODO: This is
# very hacky.  If there are better ways to do it, please let me know.
# (maybe a custom nose test loader really would be the better option?)
# -- Tobias Brox <t-caldav@tobixen.no>, 2013-10-10

_servernames = set()
for _caldav_server in caldav_servers:
    # create a unique identifier out of the server domain name
    _parsed_url = urlparse(_caldav_server['url'])
    _servername = (_parsed_url.hostname.replace('.', '_') +
                   str(_parsed_url.port or ''))
    while _servername in _servernames:
        _servername = _servername + '_'
    _servernames.add(_servername)

    # create a classname and a class
    _classname = 'TestForServer_' + _servername

    # inject the new class into this namespace
    vars()[_classname] = type(
        _classname, (RepeatedFunctionalTestsBaseClass,),
        {'server_params': _caldav_server})

class TestLocalXandikos(RepeatedFunctionalTestsBaseClass):
    """
    Sets up a local Xandikos server and Runs the functional tests towards it
    """
    def setup(self):
        if not test_xandikos:
            raise SkipTest("Skipping Xadikos test due to configuration")
        self.serverdir = tempfile.TemporaryDirectory()
        self.serverdir.__enter__()
        ## TODO - we should do something with the access logs from Xandikos
        self.backend = XandikosBackend(path=self.serverdir.name)
        self.backend.create_principal('/sometestuser/', create_defaults=True)
        self.xandikos_server = make_server(xandikos_host, xandikos_port, XandikosApp(self.backend, '/sometestuser/'))
        self.xandikos_thread = threading.Thread(target=self.xandikos_server.serve_forever)
        self.xandikos_thread.start()
        self.server_params = {'url': 'http://%s:%i/sometestuser/' % (xandikos_host, xandikos_port), 'username': 'user1', 'password': 'password1'}
        ## TODO: this should go away eventually.  Ref https://github.com/jelmer/xandikos/issues/102 support for expanded search for recurring events has been fixed in the master branch of xandikos.
        self.server_params['norecurring'] = True
        RepeatedFunctionalTestsBaseClass.setup(self)

    def teardown(self):
        if not test_xandikos:
            return
        self.xandikos_server.shutdown()
        self.xandikos_server.socket.close()
        i=0
        while (self.xandikos_thread.is_alive()):
            time.sleep(0.05)
            i+=1
            assert(i<100)
        self.serverdir.__exit__(None, None, None)
        RepeatedFunctionalTestsBaseClass.teardown(self)

class TestCalDAV:
    """
    Test class for "pure" unit tests (small internal tests, testing that
    a small unit of code works as expected, without any third party
    dependencies)
    """
    def testCalendar(self):
        """
        Principal.calendar() and CalendarSet.calendar() should create
        Calendar objects without initiating any communication with the
        server.  Calendar.event() should create Event object without
        initiating any communication with the server.

        DAVClient.__init__ also doesn't do any communication
        Principal.__init__ as well, if the principal_url is given
        Principal.calendar_home_set needs to be set or the server will be queried
        """
        cal_url = "http://me:hunter2@calendar.example:80/"
        client = DAVClient(url=cal_url)

        principal = Principal(client, cal_url + "me/")
        principal.calendar_home_set = cal_url + "me/calendars/"
        # calendar_home_set is actually a CalendarSet object
        assert(isinstance(principal.calendar_home_set, CalendarSet))
        calendar1 = principal.calendar(name="foo", cal_id="bar")
        calendar2 = principal.calendar_home_set.calendar(
            name="foo", cal_id="bar")
        assert_equal(calendar1.url, calendar2.url)
        assert_equal(
            calendar1.url, "http://calendar.example:80/me/calendars/bar")

        # principal.calendar_home_set can also be set to an object
        # This should be noop
        principal.calendar_home_set = principal.calendar_home_set
        calendar1 = principal.calendar(name="foo", cal_id="bar")
        assert_equal(calendar1.url, calendar2.url)

        # When building a calendar from a relative URL and a client,
        # the relative URL should be appended to the base URL in the client
        calendar1 = Calendar(client, 'someoneelse/calendars/main_calendar')
        calendar2 = Calendar(client,
            'http://me:hunter2@calendar.example:80/someoneelse/calendars/main_calendar')
        assert_equal(calendar1.url, calendar2.url)

    def testFailedQuery(self):
        """
        ref https://github.com/python-caldav/caldav/issues/54
        """
        cal_url = "http://me:hunter2@calendar.example:80/"
        client = DAVClient(url=cal_url)
        calhome = CalendarSet(client, cal_url + "me/")

        ## syntesize a failed response
        class FailedResp:
            pass
        failedresp = FailedResp()
        failedresp.status = 400
        failedresp.reason = "you are wrong"
        failedresp.raw = "your request does not adhere to standards"

        ## synthesize a new http method
        calhome.client.unknown_method = lambda url, body, depth: failedresp

        ## call it.
        assert_raises(error.DAVError, calhome._query, query_method='unknown_method')

    def testDefaultClient(self):
        """When no client is given to a DAVObject, but the parent is given,
        parent.client will be used"""
        cal_url = "http://me:hunter2@calendar.example:80/"
        client = DAVClient(url=cal_url)
        calhome = CalendarSet(client, cal_url + "me/")
        calendar = Calendar(parent=calhome)
        assert_equal(calendar.client, calhome.client)

    def testURL(self):
        """Exercising the URL class"""
        long_url = "http://foo:bar@www.example.com:8080/caldav.php/?foo=bar"

        # 1) URL.objectify should return a valid URL object almost no matter
        # what's thrown in
        url0 = URL.objectify(None)
        url0b= URL.objectify("")
        url1 = URL.objectify(long_url)
        url2 = URL.objectify(url1)
        url3 = URL.objectify("/bar")
        url4 = URL.objectify(urlparse(str(url1)))
        url5 = URL.objectify(urlparse("/bar"))

        # 2) __eq__ works well
        assert_equal(url1, url2)
        assert_equal(url1, url4)
        assert_equal(url3, url5)

        # 3) str will always return the URL
        assert_equal(str(url1), long_url)
        assert_equal(str(url3), "/bar")
        assert_equal(str(url4), long_url)
        assert_equal(str(url5), "/bar")

        # 4) join method
        url6 = url1.join(url2)
        url7 = url1.join(url3)
        url8 = url1.join(url4)
        url9 = url1.join(url5)
        urlA = url1.join("someuser/calendar")
        urlB = url5.join(url1)
        assert_equal(url6, url1)
        assert_equal(url7, "http://foo:bar@www.example.com:8080/bar")
        assert_equal(url8, url1)
        assert_equal(url9, url7)
        assert_equal(urlA, "http://foo:bar@www.example.com:8080/caldav.php/someuser/calendar")
        assert_equal(urlB, url1)
        assert_raises(ValueError, url1.join, "http://www.google.com")

        # 4b) join method, with URL as input parameter
        url6 = url1.join(URL.objectify(url2))
        url7 = url1.join(URL.objectify(url3))
        url8 = url1.join(URL.objectify(url4))
        url9 = url1.join(URL.objectify(url5))
        urlA = url1.join(URL.objectify("someuser/calendar"))
        urlB = url5.join(URL.objectify(url1))
        url6b= url6.join(url0)
        url6c= url6.join(url0b)
        url6d= url6.join(None)
        for url6alt in (url6b, url6c, url6d):
            assert_equal(url6, url6alt)
        assert_equal(url6, url1)
        assert_equal(url7, "http://foo:bar@www.example.com:8080/bar")
        assert_equal(url8, url1)
        assert_equal(url9, url7)
        assert_equal(urlA, "http://foo:bar@www.example.com:8080/caldav.php/someuser/calendar")
        assert_equal(urlB, url1)
        assert_raises(ValueError, url1.join, "http://www.google.com")

        # 5) all urlparse methods will work.  always.
        assert_equal(url1.scheme, 'http')
        assert_equal(url2.path, '/caldav.php/')
        assert_equal(url7.username, 'foo')
        assert_equal(url5.path, '/bar')
        urlC = URL.objectify("https://www.example.com:443/foo")
        assert_equal(urlC.port, 443)

        # 6) is_auth returns True if the URL contains a username.
        assert_equal(urlC.is_auth(), False)
        assert_equal(url7.is_auth(), True)

        # 7) unauth() strips username/password
        assert_equal(url7.unauth(), 'http://www.example.com:8080/bar')

    def testFilters(self):
        filter = \
            cdav.Filter().append(
                cdav.CompFilter("VCALENDAR").append(
                    cdav.CompFilter("VEVENT").append(
                        cdav.PropFilter("UID").append(
                            [cdav.TextMatch("pouet", negate=True)]))))
        # print(filter)

        crash = cdav.CompFilter()
        value = None
        try:
            value = str(crash)
        except:
            pass
        if value is not None:
            raise Exception("This should have crashed")
