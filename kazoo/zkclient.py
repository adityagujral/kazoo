#!/usr/bin/env python

from functools import partial
from collections import namedtuple

import zookeeper

from zookeeper import SystemErrorException, RuntimeInconsistencyException,\
    DataInconsistencyException, ConnectionLossException,\
    MarshallingErrorException,UnimplementedException,OperationTimeoutException,\
    BadArgumentsException,ApiErrorException, NoNodeException, NoAuthException,\
    BadVersionException,NoChildrenForEphemeralsException,NodeExistsException,\
    InvalidACLException, AuthFailedException, NotEmptyException,\
    SessionExpiredException, InvalidCallbackException

from kazoo.sync import get_sync_strategy

ZK_OPEN_ACL_UNSAFE = {"perms": zookeeper.PERM_ALL, "scheme": "world",
                       "id": "anyone"}


class AclPermission(object):
    READ = zookeeper.PERM_READ
    WRITE = zookeeper.PERM_WRITE
    CREATE = zookeeper.PERM_CREATE
    DELETE = zookeeper.PERM_DELETE
    ADMIN = zookeeper.PERM_ADMIN
    ALL = zookeeper.PERM_ALL


class KeeperState(object):
    ASSOCIATING = zookeeper.ASSOCIATING_STATE
    AUTH_FAILED = zookeeper.AUTH_FAILED_STATE
    CONNECTED = zookeeper.CONNECTED_STATE
    CONNECTING = zookeeper.CONNECTING_STATE
    EXPIRED_SESSION = zookeeper.EXPIRED_SESSION_STATE


class EventType(object):
    NOTWATCHING = zookeeper.NOTWATCHING_EVENT
    SESSION = zookeeper.SESSION_EVENT
    CREATED = zookeeper.CREATED_EVENT
    DELETED = zookeeper.DELETED_EVENT
    CHANGED = zookeeper.CHANGED_EVENT
    CHILD = zookeeper.CHILD_EVENT


class WatchedEvent(namedtuple('WatchedEvent', ('type', 'state', 'path'))):
    """ A change on the ZooKeeper that a Watcher is able to respond to.

    The WatchedEvent includes exactly what happened, the current state of the
    ZooKeeper, and the path of the znode that was involved in the event.
    """

class ZooKeeperClient(object):
    """A gevent-friendly wrapper of the Apache ZooKeeper zkpython client

    TODO lots to do:
    * better handling of ZK client session events
    * disconnected state handling
    * the rest of the operations
    """

    DEFAULT_TIMEOUT = 10.0

    def __init__(self, hosts, watcher=None, timeout=None, client_id=None):
        self._hosts = hosts
        self._watcher = watcher
        self._provided_client_id = client_id
        if timeout is None:
            timeout = self.DEFAULT_TIMEOUT

        # ZK uses milliseconds
        self._timeout = int(timeout * 1000)

        self._sync = get_sync_strategy()

        self._handle = None
        self._connected = False
        self._connected_async_result = self._sync.async_result()
        self._connection_timed_out = False

    @property
    def connected(self):
        return self._connected

    @property
    def client_id(self):
        if self._handle is not None:
            return zookeeper.client_id(self._handle)
        return None

    def get_sync_strategy(self):
        return self._sync

    def _wrap_session_callback(self, func):
        def wrapper(handle, type, state, path):

            event = WatchedEvent(type, state, path)
            self._sync.dispatch_callback(func, event)
        return wrapper

    def _wrap_watch_callback(self, func):
        def wrapper(handle, type, state, path):

            # don't send session events to all watchers
            if state != zookeeper.SESSION_EVENT:
                event = WatchedEvent(type, state, path)
                self._sync.dispatch_callback(func, event)
        return wrapper

    def _session_callback(self, event):
        if event.state == zookeeper.CONNECTED_STATE:
            self._connected = True
        elif event.state == zookeeper.CONNECTING_STATE:
            self._connected = False

        if not self._connected_async_result.ready():
            #close the connection if we already timed out
            if self._connection_timed_out and self._connected:
                self.close()
            else:
                self._connected_async_result.set()

        if self._watcher:
            self._watcher(event)

    def connect_async(self):
        """Asynchronously initiate connection to ZK

        @return: AsyncResult object set on connection success
        @rtype AsyncResult
        """

        cb = self._wrap_session_callback(self._session_callback)
        if self._provided_client_id:
            self._handle = zookeeper.init(self._hosts, cb, self._timeout,
                self._provided_client_id)
        else:
            self._handle = zookeeper.init(self._hosts, cb, self._timeout)

        return self._connected_async_result

    def connect(self, timeout=None):
        """Initiate connection to ZK

        @param timeout: time in seconds to wait for connection to succeed
        """
        async_result = self.connect_async()
        try:
            async_result.get(timeout=timeout)
        except self._sync.timeout_error:
            self._connection_timed_out = True
            raise

    def close(self):
        """Disconnect from ZooKeeper
        """
        if self._connected:
            code = zookeeper.close(self._handle)
            self._handle = None
            self._connected = False
            if code != zookeeper.OK:
                raise err_to_exception(code)

    def add_auth_async(self, scheme, credential):
        """Asynchronously send credentials to server

        @param scheme: authentication scheme (default supported: "digest")
        @param credential: the credential -- value depends on scheme
        @return: AsyncResult object set on completion
        @rtype AsyncResult
        """
        async_result = self._sync.async_result()
        callback = partial(_generic_callback, async_result)

        zookeeper.add_auth(self._handle, scheme, credential, callback)
        return async_result

    def add_auth(self, scheme, credential):
        """Send credentials to server

        @param scheme: authentication scheme (default supported: "digest")
        @param credential: the credential -- value depends on scheme
        """
        return self.add_auth_async(scheme, credential).get()

    def create_async(self, path, value, acl=None, ephemeral=False, sequence=False):
        """Asynchronously create a ZNode

        @param path: path of node
        @param value: initial value of node
        @param acl: permissions for node
        @param ephemeral: boolean indicating whether node is ephemeral (tied to this session)
        @param sequence: boolean indicating whether path is suffixed with a unique index
        @return: AsyncResult object set on completion with the real path of the new node
        @rtype AsyncResult
        """
        flags = 0
        if ephemeral:
            flags |= zookeeper.EPHEMERAL
        if sequence:
            flags |= zookeeper.SEQUENCE
        if acl is None:
            acl = (ZK_OPEN_ACL_UNSAFE,)

        async_result = self._sync.async_result()
        callback = partial(_generic_callback, async_result)

        zookeeper.acreate(self._handle, path, value, list(acl), flags, callback)
        return async_result

    def create(self, path, value, acl=None, ephemeral=False, sequence=False):
        """Create a ZNode

        @param path: path of node
        @param value: initial value of node
        @param acl: permissions for node
        @param ephemeral: boolean indicating whether node is ephemeral (tied to this session)
        @param sequence: boolean indicating whether path is suffixed with a unique index
        @return: real path of the new node
        """
        return self.create_async(path, value, acl, ephemeral, sequence).get()

    def exists_async(self, path, watch=None):
        """Asynchronously check if a node exists

        @param path: path of node
        @param watch: optional watch callback to set for future changes to this path
        @return stat of the node if it exists, else None
        """
        async_result = self._sync.async_result()
        callback = partial(_exists_callback, async_result)
        watch_callback = self._wrap_watch_callback(watch) if watch else None

        zookeeper.aexists(self._handle, path, watch_callback, callback)
        return async_result

    def exists(self, path, watch=None):
        """Check if a node exists

        @param path: path of node
        @param watch: optional watch callback to set for future changes to this path
        @return stat of the node if it exists, else None
        """
        return self.exists_async(path, watch).get()

    def get_async(self, path, watch=None):
        """Asynchronously get the value of a node

        @param path: path of node
        @param watch: optional watch callback to set for future changes to this path
        @return AsyncResult set with tuple (value, stat) of node on success
        @rtype AsyncResult
        """
        async_result = self._sync.async_result()
        callback = partial(_generic_callback, async_result)
        watch_callback = self._wrap_watch_callback(watch) if watch else None

        zookeeper.aget(self._handle, path, watch_callback, callback)
        return async_result

    def get(self, path, watch=None):
        """Get the value of a node

        @param path: path of node
        @param watch: optional watch callback to set for future changes to this path
        @return tuple (value, stat) of node
        """
        return self.get_async(path, watch).get()

    def get_children_async(self, path, watch=None):
        """Asynchronously get a list of child nodes of a path

        @param path: path of node to list
        @param watch: optional watch callback to set for future changes to this path
        @return: AsyncResult set with list of child node names on success
        @rtype: AsyncResult
        """
        async_result = self._sync.async_result()
        callback = partial(_generic_callback, async_result)
        watch_callback = self._wrap_watch_callback(watch) if watch else None

        zookeeper.aget_children(self._handle, path, watch_callback, callback)
        return async_result

    def get_children(self, path, watch=None):
        """Get a list of child nodes of a path

        @param path: path of node to list
        @param watch: optional watch callback to set for future changes to this path
        @return: list of child node names
        """
        return self.get_children_async(path, watch).get()

    def set_async(self, path, data, version=-1):
        """Set the value of a node

        If the version of the node being updated is newer than the supplied
        version (and the supplied version is not -1), a BadVersionException
        will be raised.

        @param path: path of node to set
        @param data: new data value
        @param version: version of node being updated, or -1
        @return: AsyncResult set with new node stat on success
        @rtype AsyncResult
        """
        async_result = self._sync.async_result()
        callback = partial(_generic_callback, async_result)

        zookeeper.aset(self._handle, path, data, version, callback)
        return async_result

    def set(self, path, data, version=-1):
        """Set the value of a node

        If the version of the node being updated is newer than the supplied
        version (and the supplied version is not -1), a BadVersionException
        will be raised.

        @param path: path of node to set
        @param data: new data value
        @param version: version of node being updated, or -1
        @return: updated node stat
        """
        return self.set_async(path, data, version).get()

    def delete_async(self, path, version=-1):
        """Asynchronously delete a node

        @param path: path of node to delete
        @param version: version of node to delete, or -1 for any
        @return AyncResult set upon completion
        @rtype AsyncResult
        """
        async_result = self._sync.async_result()
        callback = partial(_generic_callback, async_result)

        zookeeper.adelete(self._handle, path, version, callback)
        return async_result

    def delete(self, path, version=-1):
        """Delete a node

        @param path: path of node to delete
        @param version: version of node to delete, or -1 for any
        """
        self.delete_async(path, version).get()


def _generic_callback(async_result, handle, code, *args):
    if code != zookeeper.OK:
        exc = err_to_exception(code)
        async_result.set_exception(exc)
    else:
        if not args:
            result = None
        elif len(args) == 1:
            result = args[0]
        else:
            result = tuple(args)

        async_result.set(result)


def _exists_callback(async_result, handle, code, stat):
    if code not in (zookeeper.OK, zookeeper.NONODE):
        exc = err_to_exception(code)
        async_result.set_exception(exc)
    else:
        async_result.set(stat)


# this dictionary is a port of err_to_exception() from zkpython zookeeper.c
_ERR_TO_EXCEPTION = {
    zookeeper.SYSTEMERROR: SystemErrorException,
    zookeeper.RUNTIMEINCONSISTENCY: RuntimeInconsistencyException,
    zookeeper.DATAINCONSISTENCY: DataInconsistencyException,
    zookeeper.CONNECTIONLOSS: ConnectionLossException,
    zookeeper.MARSHALLINGERROR: MarshallingErrorException,
    zookeeper.UNIMPLEMENTED: UnimplementedException,
    zookeeper.OPERATIONTIMEOUT: OperationTimeoutException,
    zookeeper.BADARGUMENTS: BadArgumentsException,
    zookeeper.APIERROR: ApiErrorException,
    zookeeper.NONODE: NoNodeException,
    zookeeper.NOAUTH: NoAuthException,
    zookeeper.BADVERSION: BadVersionException,
    zookeeper.NOCHILDRENFOREPHEMERALS: NoChildrenForEphemeralsException,
    zookeeper.NODEEXISTS: NodeExistsException,
    zookeeper.INVALIDACL: InvalidACLException,
    zookeeper.AUTHFAILED: AuthFailedException,
    zookeeper.NOTEMPTY: NotEmptyException,
    zookeeper.SESSIONEXPIRED: SessionExpiredException,
    zookeeper.INVALIDCALLBACK: InvalidCallbackException,
}

def err_to_exception(error_code, msg=None):
    """Return an exception object for a Zookeeper error code
    """
    try:
        zkmsg = zookeeper.zerror(error_code)
    except Exception:
        zkmsg = ""

    if msg:
        if zkmsg:
            msg = "%s: %s" % (zkmsg, msg)
    else:
        msg = zkmsg

    exc = _ERR_TO_EXCEPTION.get(error_code)
    if exc is None:

        # double check that it isn't an ok resonse
        if error_code == zookeeper.OK:
            return None

        # otherwise generic exception
        exc = Exception
    return exc(msg)
