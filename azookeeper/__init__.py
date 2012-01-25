import os

from azookeeper.client import ZooKeeperClient

__all__ = ['ZooKeeperClient']


# ZK C client likes to spew log info to STDERR. disable that unless an
# env is present.

def disable_zookeeper_log():
    import zookeeper
    zookeeper.set_log_stream(open('/dev/null'))

if not "AZK_LOG_ENABLED" in os.environ:
    disable_zookeeper_log()

def patch_extras():
    # workaround for http://code.google.com/p/gevent/issues/detail?id=112
    # gevent isn't patching threading._sleep which causes problems
    # for Condition objects
    from gevent import sleep
    import threading
    threading._sleep = sleep

if "AZK_TEST_GEVENT_PATCH" in os.environ:
    from gevent import monkey; monkey.patch_all()
    patch_extras()