from BitTornado.download_bt1 import BT1Download, defaults, get_response
from BitTornado.RawServer import RawServer
from BitTornado.bencode import bencode
from BitTornado import createPeerID
from threading import Event
from os.path import abspath
from swarm.utils import spawn_later
import hashlib
import random
import sys


class Swarm(object):
    def __init__(self, is_seed):
        self.id = createPeerID()
        self.is_seed = is_seed

        self.done_flag = Event()
        self.done = False
        self.file = ''

        self.percent_done = 0.0
        self.download_rate = 0.0
        self.upload_rate = 0.0
        self.time_estimate = 0.0

        self.ratio = 0.0
        self.upload_total = 0.0
        self.download_total = 0.0
        self.distributed_copies = 0.0
        self.peers_percent_done = 0.0
        self.torrent_rate = 0.0

        self.num_peers = 0
        self.num_seeds = 0
        self.num_old_seeds = 0

        self.activity = ''
        self.downloadTo = ''

    def on_finish(self):
        self.done = True
        self.percent_done = 100.0
        self.download_rate = 0.0
        self.activity = 'seeding'

    def maybe_shut_down(self):
        if self.num_seeds == 0 and self.num_peers == 0:
            self.done_flag.set()
        else:
            spawn_later(5, self.maybe_shut_down)

    def on_fail(self):
        self.done = True
        self.percent_done = 0.0
        self.download_rate = 0.0
        self.activity = 'failed'
        self.done_flag.set()

    def on_error(self, errormsg):
        print 'ERROR', errormsg
        self.done_flag.set()

    def on_exception(self, excmsg):
        print 'EXCEPTION', excmsg

    # noinspection PyUnusedLocal
    def on_status(self, dpflag=Event(), fractionDone=None, timeEst=None, downRate=None, upRate=None,
                  statistics=None, spew=None, sizeDone=None, activity=None):

        if fractionDone is not None:
            self.percent_done = float(int(fractionDone * 1000)) / 10

        if timeEst is not None:
            self.time_estimate = timeEst

            m, s = divmod(timeEst, 60)
            h, m = divmod(m, 60)

            self.activity = 'downloading '

            if h > 0:
                self.activity += '%dh%02dm%02ds' % (h, m, s)
            elif m > 0:
                self.activity += '%dm%02ds' % (m, s)
            else:
                self.activity += '%02ds' % s

        if activity is not None:
            self.activity = activity

        if downRate is not None:
            self.download_rate = float(downRate) / (1 << 10)

        if upRate is not None:
            self.upload_rate = float(upRate) / (1 << 10)

        if statistics is not None:
            if self.num_peers > 0 or self.num_seeds > 0:
                if statistics.numPeers == 0 and statistics.numSeeds == 0:
                    spawn_later(5, self.maybe_shut_down)

            self.ratio = statistics.shareRating
            self.upload_total = float(statistics.upTotal) / (1 << 20)
            self.download_total = float(statistics.downTotal) / (1 << 20)
            self.num_peers = statistics.numPeers
            self.num_old_seeds = statistics.numOldSeeds
            self.num_seeds = statistics.numSeeds
            self.distributed_copies = 0.001 * int(1000 * statistics.numCopies)
            self.peers_percent_done = statistics.percentDone
            self.torrent_rate = float(statistics.torrentRate) / (1 << 10)

        if self.activity:
            print '%.2f%% complete (down: %.1f kb/s up: %.1f kB/s peers: %d seeds: %d) %s' % (
                self.percent_done, self.download_rate, self.upload_rate, self.num_peers, self.num_seeds, self.activity
            )
            sys.stdout.flush()

        dpflag.set()

    def chooseFile(self, default, size, saveas, dir):
        self.file = '%s (%.1f MB)' % (default, float(size) / (1 << 20))
        if saveas != '':
            default = saveas
        self.downloadTo = abspath(default)
        return default

    def newpath(self, path):
        self.downloadTo = path


def run(torrent_file, ip, saveas, is_seed, port):
    swarm = Swarm(is_seed)
    rawserver = None

    try:
        config = {k: v for k, v, _ in defaults}
        config['ip'] = ip
        config['responsefile'] = torrent_file
        config['saveas'] = saveas
        config['selector_enabled'] = False

        random.seed(swarm.id)

        rawserver = RawServer(
            doneflag=swarm.done_flag,
            timeout_check_interval=config['timeout_check_interval'],
            timeout=config['timeout'],
            ipv6_enable=config['ipv6_enabled'],
            failfunc=swarm.on_fail,
            errorfunc=swarm.on_exception)
        rawserver.bind(
            port=port,
            bind=config['bind'],
            reuse=True,
            ipv6_socket_style=config['ipv6_binds_v4'])

        response = get_response(
            file=config['responsefile'],
            url=config['url'],
            errorfunc=swarm.on_error)

        if not response:
            return

        dow = BT1Download(
            statusfunc=swarm.on_status,
            finfunc=swarm.on_finish,
            errorfunc=swarm.on_error,
            excfunc=swarm.on_exception,
            doneflag=swarm.done_flag,
            config=config,
            response=response,
            infohash=hashlib.sha1(bencode(response['info'])).digest(),
            id=swarm.id,
            rawserver=rawserver,
            port=port)

        if not dow.saveAs(swarm.chooseFile, swarm.newpath):
            return

        if not dow.initFiles(old_style=True):
            return

        if not dow.startEngine():
            dow.shutdown()
            return

        dow.startRerequester()
        dow.autoStats()

        if not dow.am_I_finished():
            swarm.on_status(activity='connecting to peers')

        rawserver.listen_forever(dow.getPortHandler())

        swarm.on_status(activity='shutting down')

        dow.shutdown()
    finally:
        if rawserver:
            rawserver.shutdown()

        if not swarm.done:
            swarm.on_fail()