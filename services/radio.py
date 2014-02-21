import WebSockets
import Services
import Queue
import time
import threading
import json
import ConfigParser

from WebSockets import WebSocketTransaction

class Client:
    STATE_INITIALIZE = 0
    STATE_FOLLOWING = 1
    STATE_SERVER = 2
    def __init__(self, addr, socketId, service):
        self.address = addr
        self.socketId = socketId
        self.sendQueue = service.sendQueue
        
        self.stream = service.stream
        self.service = service
        self.subscriptionId = None
        self.state = Client.STATE_INITIALIZE
        data = {"type" : "query", "query" : "mode"}
        transaction = WebSocketTransaction(WebSocketTransaction.TRANSACTION_DATA, self.socketId, json.dumps(data))
        self.sendQueue.put(transaction)
            
    def injectReceived(self, received):
        """Handles a received json string from the client"""
        #validate the packet
        if received.socketId != self.socketId:
            print "Received a packet meant for", received.socketId
            return #we can't process this one
            
        #find out what they sent us
        if received.transactionType == WebSocketTransaction.TRANSACTION_CLOSE:
            #stop listening to updates
            if self.subscriptionId != None:
                self.stream.unsubscribe(self.subscriptionId)
            return
            
        #if we made it this far, it was normal data being received
        data = json.loads(received.data)
        if self.state == Client.STATE_INITIALIZE:
            #we only want a mode given
            if "mode" in data:
                if data["mode"] == "listen":
                    #they are listening only, so can remain anonymous
                    self.state = Client.STATE_FOLLOWING
                    self.subscriptionId = self.stream.subscribe(self)
                    print "Anonymous user now listening."
                    self.stream.newEvent(Stream.StreamEvent(Stream.StreamEvent.EV_LISTENERS, {'listeners': len(self.service.clients)}))
                    return
                elif data["mode"] == "server":
                    #The server is connecting
                    if "key" in data and Service.apiKeys:
                        for name, key in Service.apiKeys:
                            if key == data["key"]:
                                self.state = Client.STATE_SERVER
                                print "Server connected: '" + name + "'"
                                return
                    print "ERROR: Invalid server connection from " + str(self.address)
                    return

            #only ask for a name if they sent us something else
            transaction = WebSocketTransaction(WebSocketTransaction.TRANSACTION_DATA, self.socketId, json.dumps({ 'type' : 'query', 'query' : 'mode' }))
            self.sendQueue.put(transaction)
            
        elif self.state == Client.STATE_SERVER:
            #we only listen to the server
            if "type" in data and data["type"] == "event" and "event" in data and "data" in data:
                print "SERVER EVENT" 
                print data["event"]
                eventData = data["data"]
                if data["event"] == "add":
                    self.stream.newEvent(Stream.StreamEvent(Stream.StreamEvent.EV_ADD, eventData))
                elif data["event"] == "delete":
                    self.stream.newEvent(Stream.StreamEvent(Stream.StreamEvent.EV_DELETE, eventData))
                elif data["event"] == "vote":
                    self.stream.newEvent(Stream.StreamEvent(Stream.StreamEvent.EV_VOTE, eventData))
                elif data["event"] == "next":
                    self.stream.newEvent(Stream.StreamEvent(Stream.StreamEvent.EV_NEXT, eventData))
                
    def onEvent(self, event):
        """Something has happened"""
        data = {}
        if event.eventId == Stream.StreamEvent.EV_ADD:
            #A song has been added. Add it
            data = { 'type' : 'event', 'event' : { 'type' : 'event', 'event' : 'add', 'song' : event.data } }
        elif event.eventId == Stream.StreamEvent.EV_DELETE:
            #A song has been deleted. Remove it
            data = { 'type' : 'event', 'event' : { 'type' : 'event', 'event' : 'delete', 'song' : event.data } }
        elif event.eventId == Stream.StreamEvent.EV_VOTE:
            #Someone has voted on a song. Update the score and position
            data = { 'type' : 'event', 'event' : { 'type' : 'event', 'event' : 'vote', 'song' : event.data } }
        elif event.eventId == Stream.StreamEvent.EV_NEXT:
            #Song has ended. Tell users to update their song
            data = { 'type' : 'event', 'event' : { 'type' : 'event', 'event' : 'next', 'song' : event.data } }
        elif event.eventId == Stream.StreamEvent.EV_LISTENERS:
            #Number of listeners has changed
            data = { 'type' : 'event', 'event' : { 'type' : 'event', 'event' : 'listeners', 'listeners' : event.data } }
        transaction = WebSocketTransaction(WebSocketTransaction.TRANSACTION_DATA, self.socketId, json.dumps(data))
        self.sendQueue.put(transaction)
                        
class Stream(Services.Subscribable):
    class StreamEvent(Services.Subscribable.SubscriptionEvent):
        """Encapsulates an event"""
        EV_ADD = 0
        EV_DELETE = 1
        EV_VOTE = 2
        EV_NEXT = 3
        EV_LISTENERS = 4
        def __init__(self, eventId, data):
            """Creates a new radio event.
            type: A value matching one of the EV_ variables in this class
            data: Data to go along with the event. If a message event, it will contain the tuple with the message data.
                  If a new subscriber event, it contains the new subscriber's name"""
            print data
            Services.Subscribable.SubscriptionEvent.__init__(self, eventId, data)
    
    def __init__(self, service):
        Services.Subscribable.__init__(self)
        self.lock = threading.Lock()
        self.service = service
        
    def subscribe(self, client):
        """Subscribes a client to the stream's events. The subscriber should implement a
        method called onEvent(event) where the argument is a Stream.StreamEvent"""
        #event = Stream.StreamEvent(Stream.StreamEvent.EV_NEWSUBSCRIBER, (client.name, self.getNumSubscribers() + 1))
        #self.sendEvent(event)
        return Services.Subscribable.subscribe(self, client, client.onEvent)
    
    def unsubscribe(self, sId):
        Services.Subscribable.unsubscribe(self, sId)
        #event = Stream.StreamEvent(Stream.StreamEvent.EV_UNSUBSCRIBE, (name, self.getNumSubscribers()))
        #self.sendEvent(event)
        
    def newEvent(self, event):
        """Creates a stream event. Only called by the server"""
        #event = Stream.StreamEvent(data.eventId, data.data)
        self.sendEvent(event)

class Service(Services.Service):
    config = None
    apiKeys = None
    
    def __init__(self, sendQueue, recvQueue):
        Services.Service.__init__(self, sendQueue, recvQueue)
        
        self.stream = Stream(self)
        self.clients = { }

    def run(self):
        """Main thread method"""
        print "### LSUCS LAN Radio Update Daemon v0.1 ###"
        
        print "Loading Config"
        Service.config = ConfigParser.ConfigParser()
        r = Service.config.read("server.config")
        if not r:
            print "ERROR: server.config not found"
            return
        Service.apiKeys = self.config.items("server-keys")

        try:
            while self.shutdownFlag.is_set() == False:
                try:
                    transaction = self.recvQueue.get_nowait()
                    self.recvQueue.task_done()
                    if transaction.transactionType == WebSockets.WebSocketTransaction.TRANSACTION_NEWSOCKET:
                        #we have a new client!
                        print "Got client from", transaction.data
                        client = Client(transaction.data, transaction.socketId, self)
                        self.clients[client.socketId] = client
                    elif transaction.transactionType == WebSockets.WebSocketTransaction.TRANSACTION_CLOSE:
                        print "Client removed"
                        del self.clients[transaction.socketId]
                    else:
                        #find the client to send this to
                        self.clients[transaction.socketId].injectReceived(transaction)
                except Queue.Empty:
                    pass
                except KeyError:
                    pass
                time.sleep(0.05)
        except KeyboardInterrupt:
            pass
        print "### LSUCS LAN Radio Update Daemon Shutting Down ###"
        
