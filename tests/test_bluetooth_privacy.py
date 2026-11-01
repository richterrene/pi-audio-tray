"""Bluetooth boot readiness and fail-closed behavior, without hardware or root."""
import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import Mock

path=Path(__file__).resolve().parents[1]/'pi/bluetooth-privacy.py'
spec=importlib.util.spec_from_file_location('bluetooth_privacy',path)
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)


class BusError(Exception):
    pass


class Properties:
    def __init__(self, **state):
        self.state=dict(Pairable=True,Discoverable=True,Powered=False)
        self.state.update(state)
        self.operations=[]
    def Set(self, interface, name, value, **kwargs):
        self.operations.append(('set',name,value))
        self.state[name]=value
    def Get(self, interface, name, **kwargs):
        self.operations.append(('get',name))
        return self.state[name]


def backend(*adapters):
    manager=types.SimpleNamespace(GetManagedObjects=lambda **kw:{'/org/bluez/hci'+str(i):{p.ADAPTER:{}} for i in range(len(adapters))})
    objects={'/':manager,**{'/org/bluez/hci'+str(i):props for i,props in enumerate(adapters)}}
    bus=types.SimpleNamespace(get_object=lambda service,path:objects[path])
    api=types.SimpleNamespace(Interface=lambda obj,interface:obj,Boolean=bool,DBusException=BusError)
    return bus,api


class PrivacyTests(unittest.TestCase):
    def test_missing_controller_is_retryable(self):
        with self.assertRaisesRegex(p.NotReady,'No Bluetooth controller'):
            p.configure(*backend())

    def test_privacy_verified_before_power_and_afterwards(self):
        props=Properties()
        p.configure(*backend(props))
        self.assertEqual(props.state,{'Pairable':False,'Discoverable':False,'Powered':True})
        self.assertEqual(props.operations[:4],[('set','Pairable',False),('set','Discoverable',False),('get','Pairable'),('get','Discoverable')])
        self.assertEqual(props.operations.count(('set','Pairable',False)),2)

    def test_second_adapter_is_also_closed(self):
        first,second=Properties(),Properties()
        p.configure(*backend(first,second))
        self.assertFalse(first.state['Pairable'])
        self.assertFalse(second.state['Discoverable'])

    def test_one_failed_setter_does_not_skip_other_switch(self):
        props=Properties()
        original=props.Set
        def set_value(interface,name,value,**kw):
            if name=='Pairable':raise BusError('not ready')
            original(interface,name,value,**kw)
        props.Set=set_value
        with self.assertRaises(p.NotReady):p.configure(*backend(props))
        self.assertFalse(props.state['Discoverable'])
        self.assertFalse(props.state['Powered'])

    def test_one_broken_adapter_does_not_skip_closing_another(self):
        first, second = Properties(), Properties()
        first.Set = lambda *args, **kwargs: None
        with self.assertRaises(p.NotReady):
            p.configure(*backend(first, second))
        self.assertFalse(second.state['Pairable'])
        self.assertFalse(second.state['Discoverable'])
        self.assertFalse(second.state['Powered'])

    def test_false_success_from_setter_is_refused(self):
        props=Properties()
        props.Set=lambda *args,**kw:None
        with self.assertRaises(p.NotReady):p.configure(*backend(props))

    def test_delayed_controller_recovers_without_reboot(self):
        clock=[0.0];calls=[]
        def sleep(delay):clock[0]+=delay
        def apply():
            calls.append(clock[0])
            if clock[0]<1:raise p.NotReady('No controller')
        p.wait_until_private(apply,timeout=3,interval=.5,now=lambda:clock[0],sleep=sleep)
        self.assertEqual(calls,[0,.5,1])

    def test_permanent_failure_times_out_without_reporting_success(self):
        clock=[0.0]
        def sleep(delay):clock[0]+=delay
        apply=Mock(side_effect=p.NotReady('No controller'))
        with self.assertRaisesRegex(RuntimeError,'timed out'):
            p.wait_until_private(apply,timeout=1,interval=.4,now=lambda:clock[0],sleep=sleep)
        self.assertEqual(clock[0],1)

    def test_non_readiness_errors_are_not_hidden_by_retries(self):
        apply=Mock(side_effect=ValueError('bad configuration'))
        with self.assertRaises(ValueError):p.wait_until_private(apply)
        self.assertEqual(apply.call_count,1)


if __name__=='__main__':unittest.main()
