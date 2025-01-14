#!/usr/bin/env python
# -*- encoding: utf-8 -*-

## YOU SHOULD MOST LIKELY NOT EDIT THIS FILE!
## Make a conf_private.py for personal configuration.
## Check conf_private.py.EXAMPLE

####################################
# Import personal test server config
####################################
try:
    from .conf_private import only_private ## legacy compatibility
    test_public_test_servers = only_private
except ImportError:
    try:
        from .conf_private import public_test_servers
        test_public_test_servers = only_private
    except ImportError:
        test_public_test_servers = False

try:
    from .conf_private import caldav_servers
except ImportError:
    caldav_servers = []

try:
    from .conf_private import test_private_test_servers
    if not test_private_test_servers:
        caldav_servers = []
except ImportError:
    pass


try:
    from .conf_private import test_xandikos, xandikos_host, xandikos_port
except ImportError:
    try:
        import xandikos
        test_xandikos = True
    except:
        test_xandikos = False
    xandikos_host = 'localhost'
    xandikos_port = 8993 ## random port above 8000


#####################
# Public test servers
#####################
if test_public_test_servers:
    
    ## TODO: this one is set up on emphemeral storage on OpenShift and
    ## then configured manually through the webui installer, it will
    ## most likely only work for some few days until it's down again.
    ## It's needed to hard-code the configuration into
    ## https://github.com/python-caldav/baikal
    
    caldav_servers.append({
        "url": "http://baikal-caldav-servers.cloudapps.bitbit.net/html/cal.php/",
        "username": "baikaluser",
        "password": "asdf"})

    # bedework:
    # * todos and journals are not properly supported -
    #   ref https://github.com/Bedework/bedework/issues/5
    # * propfind fails to return resourcetype,
    #   ref https://github.com/Bedework/bedework/issues/110
    # * date search on recurrences of recurring events doesn't work
    #   (not reported yet - TODO)
    caldav_servers.append({
        "url": "http://bedework-caldav-servers.cloudapps.bitbit.net/ucaldav/",
        "username": "vbede",
        "password": "bedework",
        "nojournal": True,
        "notodo": True,
        "nopropfind": True,
        "norecurring": True})

    caldav_servers.append({
        "url": "http://xandikos-caldav-servers.cloudapps.bitbit.net/",
        "username": "user1",
        "password": "password1",
        "norecurring": True
        })

    # radicale
    caldav_servers.append({
        "url": "http://radicale-caldav-servers.cloudapps.bitbit.net/",
        "username": "testuser",
        "password": "123",
        "nofreebusy": True,
        "nodefaultcalendar": True,
        "noproxy": True
    })

proxy = "127.0.0.1:8080"
proxy_noport = "127.0.0.1"
