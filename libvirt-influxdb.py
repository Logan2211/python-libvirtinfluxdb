#!/usr/bin/python
import logging
import time
import ConfigParser
import json
import xml.etree.ElementTree
import datetime
import libvirt
import signal

from daemon import runner
from influxdb import InfluxDBClient
from threading import Timer


class LibvirtInflux():
  def __init__(self):
    self.stdin_path = '/dev/null'
    self.stdout_path = '/dev/tty'
    self.stderr_path = '/dev/tty'
    self.pidfile_path =  '/var/run/libvirt-influxdb.pid'
    self.pidfile_timeout = 5
    self.connect_influx(
      config.get('influxdb', 'influxdb_host'),
      config.getint('influxdb', 'influxdb_port'),
      config.get('influxdb', 'influxdb_username'),
      config.get('influxdb', 'influxdb_password'),
      config.get('influxdb', 'influxdb_database')
    )
  def run(self):
    logger.info('Starting collector')
    if config.getboolean('libvirt_vcpus', 'enabled'):
      interval = config.getint('libvirt_vcpus', 'interval')
      logger.info('Enabling vcpu metrics (interval: %s)' % interval)
      self.rt_vcpus = RepeatedTimer(interval, self.libvirt_vcpus)
    if config.getboolean('libvirt_interfaces', 'enabled'):
      interval = config.getint('libvirt_interfaces', 'interval')
      logger.info('Enabling netif metrics (interval: %s)' % interval)
      self.rt_interfaces = RepeatedTimer(interval, self.libvirt_interfaces)
    if config.getboolean('libvirt_memory', 'enabled'):
      interval = config.getint('libvirt_memory', 'interval')
      logger.info('Enabling memory metrics (interval: %s)' % interval)
      self.rt_memory = RepeatedTimer(interval, self.libvirt_memory)
    if config.getboolean('libvirt_block', 'enabled'):
      interval = config.getint('libvirt_block', 'interval')
      logger.info('Enabling block device metrics (interval: %s)' % interval)
      self.rt_block = RepeatedTimer(interval, self.libvirt_block)
    while True:
      time.sleep(10)
  def connect_influx(self, host, port, username, password, database):
    self.influxdbclient = InfluxDBClient(host, port, username, password, database)
  def libvirt_vcpus(self):
    logger.debug('Collecting vcpu metrics')
    try:
      conn = libvirt.openReadOnly()
      if conn is None:
          raise Exception('Failed to open connection to the hypervisor')
      points = []
      for id in conn.listDomainsID():
        dom = conn.lookupByID(id)
        vcpus = {}
        for vcpu in dom.vcpus()[0]:
          if vcpu[1] == libvirt.VIR_VCPU_OFFLINE:
            state = "offline"
          elif vcpu[1] == libvirt.VIR_VCPU_RUNNING:
            state = "running"
          elif vcpu[1] == libvirt.VIR_VCPU_BLOCKED:
            state = "blocked"
          elif vcpu[1] == libvirt.VIR_VCPU_LAST:
            state = "last"
          else:
            state = "unknown"
  
          vcpus[vcpu[0]] = {        # virtual CPU number
            "state": state,       # value from virVcpuState
            "cpu_time": vcpu[2],  # CPU time used, in nanoseconds
            "cpu": vcpu[3]        # real CPU number, or -1 if offline
          }
          points.append({
            "measurement": 'libvirt_vcpus',
            "tags": {
              "project_uuid": self.nova_metadata(dom)['project']['uuid'],
              "project_name": self.nova_metadata(dom)['project']['name'],
              "instance_uuid": dom.UUIDString(),
              "libvirt_name": dom.name(),
              "libvirt_domid": dom.ID(),
              "cpu_number": vcpu[0]
            },
            "time": self.now(),
            "fields": {
              "state": vcpus[0]['state'],
              "cpu_time": vcpus[0]['cpu_time']
            }
          })
      logger.debug('Sending %s vcpu points to influxdb' % len(points))
      self.influxdbclient.write_points(points)
      return points
    except Exception, e:
      logger.error("error: %s" % e)
  def libvirt_interfaces(self):
    logger.debug('Collecting interfaces metrics')
    try:
      conn = libvirt.openReadOnly()
      if conn is None:
          raise Exception('Failed to open connection to the hypervisor')
      points = []
      for id in conn.listDomainsID():
        dom = conn.lookupByID(id)
        for dev in self.domain_xml(dom).findall("devices/interface/target"):
          devname = dev.get("dev")
          stats = dom.interfaceStats(devname)
          points.append({
            "measurement": "libvirt_interfaces",
            "tags": {
              "project_uuid": self.nova_metadata(dom)['project']['uuid'],
              "project_name": self.nova_metadata(dom)['project']['name'],
              "instance_uuid": dom.UUIDString(),
              "libvirt_name": dom.name(),
              "libvirt_domid": dom.ID(),
              "device_name": devname
            },
            "time": self.now(),
            "fields": {
              "rx_bytes": stats[0],
              "rx_packets": stats[1],
              "rx_errs": stats[2],
              "rx_drop": stats[3],
              "tx_bytes": stats[4],
              "tx_packets": stats[5],
              "tx_errs": stats[6],
              "tx_drop": stats[7]
            }
          })
      logger.debug('Sending %s interfaces points to influxdb' % len(points))
      self.influxdbclient.write_points(points)
      return points
    except Exception, e:
      logger.error("error: %s" % e)
  def libvirt_memory(self):
    logger.debug('Collecting memory metrics')
    try:
      conn = libvirt.openReadOnly()
      if conn is None:
        raise Exception('Failed to open connection to the hypervisor')
      points = []
      for id in conn.listDomainsID():
        dom = conn.lookupByID(id)
        points.append({
          "measurement": "libvirt_memory",
          "tags": {
            "project_uuid": self.nova_metadata(dom)['project']['uuid'],
            "project_name": self.nova_metadata(dom)['project']['name'],
            "instance_uuid": dom.UUIDString(),
            "libvirt_name": dom.name(),
            "libvirt_domid": dom.ID()
          },
          "time": self.now(),
          "fields": {
            "actual": dom.memoryStats()['actual'],
            "rss": dom.memoryStats()['rss']
          }
        })
      logger.debug('Sending %s memory points to influxdb' % len(points))
      self.influxdbclient.write_points(points)
      return points
    except Exception, e:
      logger.error("error: %s" % e)
  def libvirt_block(self):
    logger.debug('Collecting block device metrics')
    try:
      conn = libvirt.openReadOnly()
      if conn is None:
        raise Exception('Failed to open connection to the hypervisor')
      points = []
      for id in conn.listDomainsID():
        dom = conn.lookupByID(id)
        for dev in self.domain_xml(dom).findall("devices/disk/target"):
          devname = dev.get("dev")
          stats = dom.blockStats(devname)
          points.append({
            "measurement": "libvirt_block",
            "tags": {
              "project_uuid": self.nova_metadata(dom)['project']['uuid'],
              "project_name": self.nova_metadata(dom)['project']['name'],
              "instance_uuid": dom.UUIDString(),
              "libvirt_name": dom.name(),
              "libvirt_domid": dom.ID()
            },
            "time": self.now(),
            "fields": {
              "read_ops": stats[0],
              "read_bytes": stats[1],
              "write_ops": stats[2],
              "write_bytes": stats[3],
              "errors": stats[4]
            }
          })
      logger.debug('Sending %s block device points to influxdb' % len(points))
      self.influxdbclient.write_points(points)
      return points
    except Exception, e:
      logger.error("error: %s" % e)
  def now(self):
    return datetime.datetime.now().isoformat()
  def nova_metadata(self, domain):
      metadata = xml.etree.ElementTree.fromstring(
          domain.metadata(libvirt.VIR_DOMAIN_METADATA_ELEMENT,
                          "http://openstack.org/xmlns/libvirt/nova/1.0"))
      return {
          "name": metadata.find("name").text,
          "project": {
              "uuid": metadata.find("owner/project").get("uuid"),
              "name": metadata.find("owner/project").text
          }
      }
  def domain_xml(self, domain):
    return xml.etree.ElementTree.fromstring(domain.XMLDesc())
  def handle_exit(self, signum, frame):
    logger.info("Shutting down")
    if self.rt_vcpus:
      self.rt_vcpus.stop()
    if self.rt_interfaces:
      self.rt_interfaces.stop()
    if self.rt_memory:
      self.rt_memory.stop()
    if self.rt_block:
      self.rt_block.stop()
  

class RepeatedTimer(object):
  def __init__(self, interval, function, *args, **kwargs):
    self._timer     = None
    self.interval   = interval
    self.function   = function
    self.args       = args
    self.kwargs     = kwargs
    self.is_running = False
    self.start()
  def _run(self):
    self.is_running = False
    self.start()
    self.function(*self.args, **self.kwargs)
  def start(self):
    if not self.is_running:
      self._timer = Timer(self.interval, self._run)
      self._timer.start()
      self.is_running = True
  def stop(self):
    self._timer.cancel()
    self.is_running = False

config = ConfigParser.ConfigParser({
  'influxdb_port': '8086',
  'interval': '60',
  'enabled': 'True',
  'loglevel': 'INFO'
})
config.read('libvirt-influxdb.ini')

app = LibvirtInflux()
logger = logging.getLogger("libvirtinflux")
logger.setLevel(config.get('DEFAULT', 'loglevel'))
formatter = logging.Formatter('%(asctime)s %(name)-12s: %(levelname)-8s %(message)s')
handler = logging.FileHandler("/var/log/libvirt-influxdb.log")
handler.setFormatter(formatter)
logger.addHandler(handler)
daemon_runner = runner.DaemonRunner(app)
daemon_runner.daemon_context.files_preserve=[handler.stream]
daemon_runner.daemon_context.signal_map = {signal.SIGUSR1: app.handle_exit}
daemon_runner.do_action()
