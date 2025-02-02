# -*- coding: utf-8 -*-
#
# Copyright 2012-2015 Spotify AB
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import datetime
from helpers import unittest, LuigiTestCase
from datetime import timedelta

import luigi
import luigi.date_interval
import luigi.interface
import luigi.notifications
from helpers import with_config, LuigiTestCase
from luigi.mock import MockTarget, MockFileSystem
from luigi.parameter import ParameterException
from worker_test import email_patch

luigi.notifications.DEBUG = True


class A(luigi.Task):
    p = luigi.IntParameter()


class WithDefault(luigi.Task):
    x = luigi.Parameter(default='xyz')


class Foo(luigi.Task):
    bar = luigi.Parameter()
    p2 = luigi.IntParameter()
    not_a_param = "lol"


class Baz(luigi.Task):
    bool = luigi.BoolParameter()

    def run(self):
        Baz._val = self.bool


class ForgotParam(luigi.Task):
    param = luigi.Parameter()

    def run(self):
        pass


class ForgotParamDep(luigi.Task):

    def requires(self):
        return ForgotParam()

    def run(self):
        pass


class BananaDep(luigi.Task):
    x = luigi.Parameter()
    y = luigi.Parameter(default='def')

    def output(self):
        return MockTarget('banana-dep-%s-%s' % (self.x, self.y))

    def run(self):
        self.output().open('w').close()


class Banana(luigi.Task):
    x = luigi.Parameter()
    y = luigi.Parameter()
    style = luigi.Parameter(default=None)

    def requires(self):
        if self.style is None:
            return BananaDep()  # will fail
        elif self.style == 'x-arg':
            return BananaDep(self.x)
        elif self.style == 'y-kwarg':
            return BananaDep(y=self.y)
        elif self.style == 'x-arg-y-arg':
            return BananaDep(self.x, self.y)
        else:
            raise Exception('unknown style')

    def output(self):
        return MockTarget('banana-%s-%s' % (self.x, self.y))

    def run(self):
        self.output().open('w').close()


class MyConfig(luigi.Config):
    mc_p = luigi.IntParameter()
    mc_q = luigi.IntParameter(default=73)


class MyConfigWithoutSection(luigi.Config):
    use_cmdline_section = False
    mc_r = luigi.IntParameter()
    mc_s = luigi.IntParameter(default=99)


class NoopTask(luigi.Task):
    pass


class ParameterTest(unittest.TestCase):

    def test_default_param(self):
        self.assertEqual(WithDefault().x, 'xyz')

    def test_missing_param(self):
        def create_a():
            return A()
        self.assertRaises(luigi.parameter.MissingParameterException, create_a)

    def test_unknown_param(self):
        def create_a():
            return A(p=5, q=4)
        self.assertRaises(luigi.parameter.UnknownParameterException, create_a)

    def test_unknown_param_2(self):
        def create_a():
            return A(1, 2, 3)
        self.assertRaises(luigi.parameter.UnknownParameterException, create_a)

    def test_duplicated_param(self):
        def create_a():
            return A(5, p=7)
        self.assertRaises(luigi.parameter.DuplicateParameterException, create_a)

    def test_parameter_registration(self):
        self.assertEqual(len(Foo.get_params()), 2)

    def test_task_creation(self):
        f = Foo("barval", p2=5)
        self.assertEqual(len(f.get_params()), 2)
        self.assertEqual(f.bar, "barval")
        self.assertEqual(f.p2, 5)
        self.assertEqual(f.not_a_param, "lol")

    def test_bool_false(self):
        luigi.run(['--local-scheduler', '--no-lock', 'Baz'])
        self.assertEqual(Baz._val, False)

    def test_bool_true(self):
        luigi.run(['--local-scheduler', '--no-lock', 'Baz', '--bool'])
        self.assertEqual(Baz._val, True)

    def test_forgot_param(self):
        self.assertRaises(luigi.parameter.MissingParameterException, luigi.run, ['--local-scheduler', '--no-lock', 'ForgotParam'],)

    @email_patch
    def test_forgot_param_in_dep(self, emails):
        # A programmatic missing parameter will cause an error email to be sent
        luigi.run(['--local-scheduler', '--no-lock', 'ForgotParamDep'])
        self.assertNotEquals(emails, [])

    def test_default_param_cmdline(self):
        luigi.run(['--local-scheduler', '--no-lock', 'WithDefault'])
        self.assertEqual(WithDefault().x, 'xyz')

    def test_insignificant_parameter(self):
        class InsignificantParameterTask(luigi.Task):
            foo = luigi.Parameter(significant=False, default='foo_default')
            bar = luigi.Parameter()

        t1 = InsignificantParameterTask(foo='x', bar='y')
        self.assertEqual(t1.task_id, 'InsignificantParameterTask(bar=y)')

        t2 = InsignificantParameterTask('u', 'z')
        self.assertEqual(t2.foo, 'u')
        self.assertEqual(t2.bar, 'z')
        self.assertEqual(t2.task_id, 'InsignificantParameterTask(bar=z)')

    def test_local_significant_param(self):
        """ Obviously, if anything should be positional, so should local
        significant parameters """
        class MyTask(luigi.Task):
            # This could typically be "--label-company=disney"
            x = luigi.Parameter(significant=True)

        MyTask('arg')
        self.assertRaises(luigi.parameter.MissingParameterException,
                          lambda: MyTask())

    def test_local_insignificant_param(self):
        """ Ensure we have the same behavior as in before a78338c  """
        class MyTask(luigi.Task):
            # This could typically be "--num-threads=True"
            x = luigi.Parameter(significant=False)

        MyTask('arg')
        self.assertRaises(luigi.parameter.MissingParameterException,
                          lambda: MyTask())

    def test_nonpositional_param(self):
        """ Ensure we have the same behavior as in before a78338c  """
        class MyTask(luigi.Task):
            # This could typically be "--num-threads=True"
            x = luigi.Parameter(significant=False, positional=False)

        MyTask(x='arg')
        self.assertRaises(luigi.parameter.UnknownParameterException,
                          lambda: MyTask('arg'))


class TestNewStyleGlobalParameters(unittest.TestCase):

    def setUp(self):
        super(TestNewStyleGlobalParameters, self).setUp()
        MockTarget.fs.clear()
        BananaDep.y._reset_global()

    def expect_keys(self, expected):
        self.assertEquals(set(MockTarget.fs.get_all_data().keys()), set(expected))

    def test_x_arg(self):
        luigi.run(['--local-scheduler', '--no-lock', 'Banana', '--x', 'foo', '--y', 'bar', '--style', 'x-arg'])
        self.expect_keys(['banana-foo-bar', 'banana-dep-foo-def'])

    def test_x_arg_override(self):
        luigi.run(['--local-scheduler', '--no-lock', 'Banana', '--x', 'foo', '--y', 'bar', '--style', 'x-arg', '--BananaDep-y', 'xyz'])
        self.expect_keys(['banana-foo-bar', 'banana-dep-foo-xyz'])

    def test_x_arg_override_stupid(self):
        luigi.run(['--local-scheduler', '--no-lock', 'Banana', '--x', 'foo', '--y', 'bar', '--style', 'x-arg', '--BananaDep-x', 'blabla'])
        self.expect_keys(['banana-foo-bar', 'banana-dep-foo-def'])

    def test_x_arg_y_arg(self):
        luigi.run(['--local-scheduler', '--no-lock', 'Banana', '--x', 'foo', '--y', 'bar', '--style', 'x-arg-y-arg'])
        self.expect_keys(['banana-foo-bar', 'banana-dep-foo-bar'])

    def test_x_arg_y_arg_override(self):
        luigi.run(['--local-scheduler', '--no-lock', 'Banana', '--x', 'foo', '--y', 'bar', '--style', 'x-arg-y-arg', '--BananaDep-y', 'xyz'])
        self.expect_keys(['banana-foo-bar', 'banana-dep-foo-bar'])

    def test_x_arg_y_arg_override_all(self):
        luigi.run(['--local-scheduler', '--no-lock', 'Banana', '--x', 'foo', '--y', 'bar', '--style', 'x-arg-y-arg', '--BananaDep-y', 'xyz', '--BananaDep-x', 'blabla'])
        self.expect_keys(['banana-foo-bar', 'banana-dep-foo-bar'])

    def test_y_arg_override(self):
        luigi.run(['--local-scheduler', '--no-lock', 'Banana', '--x', 'foo', '--y', 'bar', '--style', 'y-kwarg', '--BananaDep-x', 'xyz'])
        self.expect_keys(['banana-foo-bar', 'banana-dep-xyz-bar'])

    def test_y_arg_override_both(self):
        luigi.run(['--local-scheduler', '--no-lock', 'Banana', '--x', 'foo', '--y', 'bar', '--style', 'y-kwarg', '--BananaDep-x', 'xyz', '--BananaDep-y', 'blah'])
        self.expect_keys(['banana-foo-bar', 'banana-dep-xyz-bar'])

    def test_y_arg_override_banana(self):
        luigi.run(['--local-scheduler', '--no-lock', 'Banana', '--y', 'bar', '--style', 'y-kwarg', '--BananaDep-x', 'xyz', '--Banana-x', 'baz'])
        self.expect_keys(['banana-baz-bar', 'banana-dep-xyz-bar'])


class TestRemoveGlobalParameters(unittest.TestCase):

    def setUp(self):
        super(TestRemoveGlobalParameters, self).setUp()
        MyConfig.mc_p._reset_global()
        MyConfig.mc_q._reset_global()
        MyConfigWithoutSection.mc_r._reset_global()
        MyConfigWithoutSection.mc_s._reset_global()

    def run_and_check(self, args):
        run_exit_status = luigi.run(['--local-scheduler', '--no-lock'] + args)
        self.assertTrue(run_exit_status)
        return run_exit_status

    def test_use_config_class_1(self):
        self.run_and_check(['--MyConfig-mc-p', '99', '--mc-r', '55', 'NoopTask'])
        self.assertEqual(MyConfig().mc_p, 99)
        self.assertEqual(MyConfig().mc_q, 73)
        self.assertEqual(MyConfigWithoutSection().mc_r, 55)
        self.assertEqual(MyConfigWithoutSection().mc_s, 99)

    def test_use_config_class_2(self):
        self.run_and_check(['NoopTask', '--MyConfig-mc-p', '99', '--mc-r', '55'])
        self.assertEqual(MyConfig().mc_p, 99)
        self.assertEqual(MyConfig().mc_q, 73)
        self.assertEqual(MyConfigWithoutSection().mc_r, 55)
        self.assertEqual(MyConfigWithoutSection().mc_s, 99)

    def test_use_config_class_more_args(self):
        self.run_and_check(['--MyConfig-mc-p', '99', '--mc-r', '55', 'NoopTask', '--mc-s', '123', '--MyConfig-mc-q', '42'])
        self.assertEqual(MyConfig().mc_p, 99)
        self.assertEqual(MyConfig().mc_q, 42)
        self.assertEqual(MyConfigWithoutSection().mc_r, 55)
        self.assertEqual(MyConfigWithoutSection().mc_s, 123)

    @with_config({"MyConfig": {"mc_p": "666", "mc_q": "777"}})
    def test_use_config_class_with_configuration(self):
        self.run_and_check(['--mc-r', '555', 'NoopTask'])
        self.assertEqual(MyConfig().mc_p, 666)
        self.assertEqual(MyConfig().mc_q, 777)
        self.assertEqual(MyConfigWithoutSection().mc_r, 555)
        self.assertEqual(MyConfigWithoutSection().mc_s, 99)

    @with_config({"MyConfigWithoutSection": {"mc_r": "999", "mc_s": "888"}})
    def test_use_config_class_with_configuration_2(self):
        self.run_and_check(['NoopTask', '--MyConfig-mc-p', '222', '--mc-r', '555'])
        self.assertEqual(MyConfig().mc_p, 222)
        self.assertEqual(MyConfig().mc_q, 73)
        self.assertEqual(MyConfigWithoutSection().mc_r, 555)
        self.assertEqual(MyConfigWithoutSection().mc_s, 888)

    def test_misc_1(self):
        class Dogs(luigi.Config):
            n_dogs = luigi.IntParameter()

        class CatsWithoutSection(luigi.Config):
            use_cmdline_section = False
            n_cats = luigi.IntParameter()

        self.run_and_check(['--n-cats', '123', '--Dogs-n-dogs', '456', 'WithDefault'])
        self.assertEqual(Dogs().n_dogs, 456)
        self.assertEqual(CatsWithoutSection().n_cats, 123)

        self.run_and_check(['WithDefault', '--n-cats', '321', '--Dogs-n-dogs', '654'])
        self.assertEqual(Dogs().n_dogs, 654)
        self.assertEqual(CatsWithoutSection().n_cats, 321)

    def test_global_significant_param(self):
        """ We don't want any kind of global param to be positional """
        class MyTask(luigi.Task):
            # This could typically be called "--test-dry-run"
            x_g1 = luigi.Parameter(default='y', is_global=True, significant=True)

        self.assertRaises(luigi.parameter.UnknownParameterException,
                          lambda: MyTask('arg'))

    def test_global_insignificant_param(self):
        """ We don't want any kind of global param to be positional """
        class MyTask(luigi.Task):
            # This could typically be "--yarn-pool=development"
            x_g2 = luigi.Parameter(default='y', is_global=True, significant=False)

        self.assertRaises(luigi.parameter.UnknownParameterException,
                          lambda: MyTask('arg'))


class TestParamWithDefaultFromConfig(LuigiTestCase):

    def testNoSection(self):
        self.assertRaises(ParameterException, lambda: luigi.Parameter(config_path=dict(section="foo", name="bar"))._value)

    @with_config({"foo": {}})
    def testNoValue(self):
        self.assertRaises(ParameterException, lambda: luigi.Parameter(config_path=dict(section="foo", name="bar"))._value)

    @with_config({"foo": {"bar": "baz"}})
    def testDefault(self):
        class A(luigi.Task):
            p = luigi.Parameter(config_path=dict(section="foo", name="bar"))

        self.assertEqual("baz", A().p)
        self.assertEqual("boo", A(p="boo").p)

    @with_config({"foo": {"bar": "2001-02-03T04"}})
    def testDateHour(self):
        p = luigi.DateHourParameter(config_path=dict(section="foo", name="bar"))
        self.assertEqual(datetime.datetime(2001, 2, 3, 4, 0, 0), p._value)

    @with_config({"foo": {"bar": "2001-02-03T0430"}})
    def testDateMinute(self):
        p = luigi.DateMinuteParameter(config_path=dict(section="foo", name="bar"))
        self.assertEqual(datetime.datetime(2001, 2, 3, 4, 30, 0), p._value)

    @with_config({"foo": {"bar": "2001-02-03T04H30"}})
    def testDateMinuteDeprecated(self):
        p = luigi.DateMinuteParameter(config_path=dict(section="foo", name="bar"))
        self.assertEqual(datetime.datetime(2001, 2, 3, 4, 30, 0), p._value)

    @with_config({"foo": {"bar": "2001-02-03"}})
    def testDate(self):
        p = luigi.DateParameter(config_path=dict(section="foo", name="bar"))
        self.assertEqual(datetime.date(2001, 2, 3), p._value)

    @with_config({"foo": {"bar": "2015-07"}})
    def testMonthParameter(self):
        p = luigi.MonthParameter(config_path=dict(section="foo", name="bar"))
        self.assertEqual(datetime.date(2015, 7, 1), p._value)

    @with_config({"foo": {"bar": "2015"}})
    def testYearParameter(self):
        p = luigi.YearParameter(config_path=dict(section="foo", name="bar"))
        self.assertEqual(datetime.date(2015, 1, 1), p._value)

    @with_config({"foo": {"bar": "123"}})
    def testInt(self):
        p = luigi.IntParameter(config_path=dict(section="foo", name="bar"))
        self.assertEqual(123, p._value)

    @with_config({"foo": {"bar": "true"}})
    def testBool(self):
        p = luigi.BoolParameter(config_path=dict(section="foo", name="bar"))
        self.assertEqual(True, p._value)

    @with_config({"foo": {"bar": "2001-02-03-2001-02-28"}})
    def testDateInterval(self):
        p = luigi.DateIntervalParameter(config_path=dict(section="foo", name="bar"))
        expected = luigi.date_interval.Custom.parse("2001-02-03-2001-02-28")
        self.assertEqual(expected, p._value)

    @with_config({"foo": {"bar": "1 day"}})
    def testTimeDelta(self):
        p = luigi.TimeDeltaParameter(config_path=dict(section="foo", name="bar"))
        self.assertEqual(timedelta(days=1), p._value)

    @with_config({"foo": {"bar": "2 seconds"}})
    def testTimeDeltaPlural(self):
        p = luigi.TimeDeltaParameter(config_path=dict(section="foo", name="bar"))
        self.assertEqual(timedelta(seconds=2), p._value)

    @with_config({"foo": {"bar": "3w 4h 5m"}})
    def testTimeDeltaMultiple(self):
        p = luigi.TimeDeltaParameter(config_path=dict(section="foo", name="bar"))
        self.assertEqual(timedelta(weeks=3, hours=4, minutes=5), p._value)

    @with_config({"foo": {"bar": "P4DT12H30M5S"}})
    def testTimeDelta8601(self):
        p = luigi.TimeDeltaParameter(config_path=dict(section="foo", name="bar"))
        self.assertEqual(timedelta(days=4, hours=12, minutes=30, seconds=5), p._value)

    @with_config({"foo": {"bar": "P5D"}})
    def testTimeDelta8601NoTimeComponent(self):
        p = luigi.TimeDeltaParameter(config_path=dict(section="foo", name="bar"))
        self.assertEqual(timedelta(days=5), p._value)

    @with_config({"foo": {"bar": "P5W"}})
    def testTimeDelta8601Weeks(self):
        p = luigi.TimeDeltaParameter(config_path=dict(section="foo", name="bar"))
        self.assertEqual(timedelta(weeks=5), p._value)

    @with_config({"foo": {"bar": "P3Y6M4DT12H30M5S"}})
    def testTimeDelta8601YearMonthNotSupported(self):
        def f():
            return luigi.TimeDeltaParameter(config_path=dict(section="foo", name="bar"))._value
        self.assertRaises(luigi.parameter.ParameterException, f)  # ISO 8601 durations with years or months are not supported

    @with_config({"foo": {"bar": "PT6M"}})
    def testTimeDelta8601MAfterT(self):
        p = luigi.TimeDeltaParameter(config_path=dict(section="foo", name="bar"))
        self.assertEqual(timedelta(minutes=6), p._value)

    @with_config({"foo": {"bar": "P6M"}})
    def testTimeDelta8601MBeforeT(self):
        def f():
            return luigi.TimeDeltaParameter(config_path=dict(section="foo", name="bar"))._value
        self.assertRaises(luigi.parameter.ParameterException, f)  # ISO 8601 durations with months are not supported

    def testHasDefaultNoSection(self):
        self.assertFalse(luigi.Parameter(config_path=dict(section="foo", name="bar"))._has_value)

    @with_config({"foo": {}})
    def testHasDefaultNoValue(self):
        self.assertFalse(luigi.Parameter(config_path=dict(section="foo", name="bar"))._has_value)

    @with_config({"foo": {"bar": "baz"}})
    def testHasDefaultWithBoth(self):
        self.assertTrue(luigi.Parameter(config_path=dict(section="foo", name="bar"))._has_value)

    @with_config({"foo": {"bar": "baz"}})
    def testWithDefault(self):
        p = luigi.Parameter(config_path=dict(section="foo", name="bar"), default='blah')
        self.assertEqual('baz', p._value)  # config overrides default

    def testWithDefaultAndMissing(self):
        p = luigi.Parameter(config_path=dict(section="foo", name="bar"), default='blah')
        self.assertEqual('blah', p._value)

    @with_config({"A": {"p": "p_default"}})
    def testDefaultFromTaskName(self):
        class A(luigi.Task):
            p = luigi.Parameter()

        self.assertEqual("p_default", A().p)
        self.assertEqual("boo", A(p="boo").p)

    @with_config({"A": {"p": "999"}})
    def testDefaultFromTaskNameInt(self):
        class A(luigi.Task):
            p = luigi.IntParameter()

        self.assertEqual(999, A().p)
        self.assertEqual(777, A(p=777).p)

    @with_config({"A": {"p": "p_default"}, "foo": {"bar": "baz"}})
    def testDefaultFromConfigWithTaskNameToo(self):
        class A(luigi.Task):
            p = luigi.Parameter(config_path=dict(section="foo", name="bar"))

        self.assertEqual("p_default", A().p)
        self.assertEqual("boo", A(p="boo").p)

    @with_config({"A": {"p": "p_default_2"}})
    def testDefaultFromTaskNameWithDefault(self):
        class A(luigi.Task):
            p = luigi.Parameter(default="banana")

        self.assertEqual("p_default_2", A().p)
        self.assertEqual("boo_2", A(p="boo_2").p)

    @with_config({"MyClass": {"p_wohoo": "p_default_3"}})
    def testWithLongParameterName(self):
        class MyClass(luigi.Task):
            p_wohoo = luigi.Parameter(default="banana")

        self.assertEqual("p_default_3", MyClass().p_wohoo)
        self.assertEqual("boo_2", MyClass(p_wohoo="boo_2").p_wohoo)

    @with_config({"RangeDaily": {"days_back": "123"}})
    def testSettingOtherMember(self):
        class A(luigi.Task):
            pass

        self.assertEqual(123, luigi.tools.range.RangeDaily(of=A).days_back)
        self.assertEqual(70, luigi.tools.range.RangeDaily(of=A, days_back=70).days_back)

    @with_config({"MyClass": {"p_not_global": "123"}})
    def testCommandLineWithDefault(self):
        """
        Verify that we also read from the config when we build tasks from the
        command line parsers.
        """
        class MyClass(luigi.Task):
            p_not_global = luigi.Parameter(default='banana')

            def complete(self):
                import sys
                luigi.configuration.get_config().write(sys.stdout)
                if self.p_not_global != "123":
                    raise ValueError("The parameter didn't get set!!")
                return True

            def run(self):
                pass

        self.assertTrue(self.run_locally(['MyClass']))
        self.assertFalse(self.run_locally(['MyClass', '--p-not-global', '124']))
        self.assertFalse(self.run_locally(['MyClass', '--MyClass-p-not-global', '124']))

    @with_config({"MyClass2": {"p_not_global_no_default": "123"}})
    def testCommandLineNoDefault(self):
        """
        Verify that we also read from the config when we build tasks from the
        command line parsers.
        """
        class MyClass2(luigi.Task):
            """ TODO: Make luigi clean it's register for tests. Hate this 2 dance. """
            p_not_global_no_default = luigi.Parameter()

            def complete(self):
                import sys
                luigi.configuration.get_config().write(sys.stdout)
                luigi.configuration.get_config().write(sys.stdout)
                if self.p_not_global_no_default != "123":
                    raise ValueError("The parameter didn't get set!!")
                return True

            def run(self):
                pass

        self.assertTrue(self.run_locally(['MyClass2']))
        self.assertFalse(self.run_locally(['MyClass2', '--p-not-global-no-default', '124']))
        self.assertFalse(self.run_locally(['MyClass2', '--MyClass2-p-not-global-no-default', '124']))

    @with_config({"mynamespace.A": {"p": "999"}})
    def testWithNamespaceConfig(self):
        class A(luigi.Task):
            task_namespace = 'mynamespace'
            p = luigi.IntParameter()

        self.assertEqual(999, A().p)
        self.assertEqual(777, A(p=777).p)

    def testWithNamespaceCli(self):
        class A(luigi.Task):
            task_namespace = 'mynamespace'
            p = luigi.IntParameter(default=100)
            expected = luigi.IntParameter()

            def complete(self):
                if self.p != self.expected:
                    raise ValueError
                return True

        self.assertTrue(self.run_locally_split('mynamespace.A --expected 100'))
        # TODO(arash): Why is `--p 200` hanging with multiprocessing stuff?
        # self.assertTrue(self.run_locally_split('mynamespace.A --p 200 --expected 200'))
        self.assertTrue(self.run_locally_split('mynamespace.A --mynamespace.A-p 200 --expected 200'))
        self.assertFalse(self.run_locally_split('mynamespace.A --A-p 200 --expected 200'))


class OverrideEnvStuff(unittest.TestCase):

    def setUp(self):
        env_params_cls = luigi.interface.core
        env_params_cls.scheduler_port._reset_global()

    @with_config({"core": {"default-scheduler-port": '6543'}})
    def testOverrideSchedulerPort(self):
        env_params = luigi.interface.core()
        self.assertEqual(env_params.scheduler_port, 6543)

    @with_config({"core": {"scheduler-port": '6544'}})
    def testOverrideSchedulerPort2(self):
        env_params = luigi.interface.core()
        self.assertEqual(env_params.scheduler_port, 6544)

    @with_config({"core": {"scheduler_port": '6545'}})
    def testOverrideSchedulerPort3(self):
        env_params = luigi.interface.core()
        self.assertEqual(env_params.scheduler_port, 6545)


class TestSerializeDateParameters(unittest.TestCase):

    def testSerialize(self):
        date = datetime.date(2013, 2, 3)
        self.assertEquals(luigi.DateParameter().serialize(date), '2013-02-03')
        self.assertEquals(luigi.YearParameter().serialize(date), '2013')
        self.assertEquals(luigi.MonthParameter().serialize(date), '2013-02')
        dt = datetime.datetime(2013, 2, 3, 4, 5)
        self.assertEquals(luigi.DateHourParameter().serialize(dt), '2013-02-03T04')


class TestTaskParameter(LuigiTestCase):

    def testUsage(self):

        class MetaTask(luigi.Task):
            task_namespace = "mynamespace"
            a = luigi.TaskParameter()

            def run(self):
                self.__class__.saved_value = self.a

        class OtherTask(luigi.Task):
            task_namespace = "other_namespace"

        self.assertEqual(MetaTask(a=MetaTask).a, MetaTask)
        self.assertEqual(MetaTask(a=OtherTask).a, OtherTask)

        # So I first thought this "should" work, but actually it should not,
        # because it should not need to parse values known at run-time
        self.assertNotEqual(MetaTask(a="mynamespace.MetaTask").a, MetaTask)

        # But is should be able to parse command line arguments
        self.assertRaises(luigi.task_register.TaskClassNotFoundException,
                          lambda: (luigi.run(['--local-scheduler', '--no-lock'] +
                                   'mynamespace.MetaTask --a blah'.split())))
        self.assertRaises(luigi.task_register.TaskClassNotFoundException,
                          lambda: (luigi.run(['--local-scheduler', '--no-lock'] +
                                   'mynamespace.MetaTask --a Taskk'.split())))
        self.assertTrue(luigi.run(['--local-scheduler', '--no-lock'] + 'mynamespace.MetaTask --a mynamespace.MetaTask'.split()))
        self.assertEqual(MetaTask.saved_value, MetaTask)
        self.assertTrue(luigi.run(['--local-scheduler', '--no-lock'] + 'mynamespace.MetaTask --a other_namespace.OtherTask'.split()))
        self.assertEqual(MetaTask.saved_value, OtherTask)
