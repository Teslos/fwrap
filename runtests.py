#!/usr/bin/python

import os, sys, re, shutil, unittest, doctest

WITH_CYTHON = True

from distutils.dist import Distribution
from distutils.core import Extension
from distutils.command.build_ext import build_ext as _build_ext
distutils_distro = Distribution()

TEST_DIRS = ['compile', 'errors', 'run', 'pyregr']
TEST_RUN_DIRS = ['run', 'pyregr']

# Lists external modules, and a matcher matching tests
# which should be excluded if the module is not present.
EXT_DEP_MODULES = {
    'numpy' : re.compile('.*\.numpy_.*').match
}

def get_numpy_include_dirs():
    import numpy
    return [numpy.get_include()]

EXT_DEP_INCLUDES = [
    # test name matcher , callable returning list
    (re.compile('numpy_.*').match, get_numpy_include_dirs),
]

VER_DEP_MODULES = {
# such as:
#    (2,4) : lambda x: x in ['run.set']
}

INCLUDE_DIRS = [ d for d in os.getenv('INCLUDE', '').split(os.pathsep) if d ]
CFLAGS = os.getenv('CFLAGS', '').split()

class build_ext(_build_ext):
    def build_extension(self, ext):
        if ext.language == 'c++':
            try:
                self.compiler.compiler_so.remove('-Wstrict-prototypes')
            except Exception:
                pass
        _build_ext.build_extension(self, ext)

class ErrorWriter(object):
    match_error = re.compile('(warning:)?(?:.*:)?\s*([-0-9]+)\s*:\s*([-0-9]+)\s*:\s*(.*)').match
    def __init__(self):
        self.output = []
        self.write = self.output.append

    def _collect(self, collect_errors, collect_warnings):
        s = ''.join(self.output)
        result = []
        for line in s.split('\n'):
            match = self.match_error(line)
            if match:
                is_warning, line, column, message = match.groups()
                if (is_warning and collect_warnings) or \
                        (not is_warning and collect_errors):
                    result.append( (int(line), int(column), message.strip()) )
        result.sort()
        return [ "%d:%d: %s" % values for values in result ]

    def geterrors(self):
        return self._collect(True, False)

    def getwarnings(self):
        return self._collect(False, True)

    def getall(self):
        return self._collect(True, True)

class TestBuilderBase(object):
    def __init__(self, *args, **kwargs):
        pass

    def build_suite(self):
        pass

class FwrapTestBuilder(object):
    def __init__(self, rootdir, workdir, selectors, exclude_selectors,
            cleanup_workdir, cleanup_sharedlibs):
        self.rootdir = rootdir
        self.workdir = workdir
        self.selectors = selectors
        self.exclude_selectors = exclude_selectors
        self.cleanup_workdir = cleanup_workdir
        self.cleanup_sharedlibs = cleanup_sharedlibs

    def build_suite(self):
        suite = unittest.TestSuite()
        test_dirs = TEST_DIRS
        filenames = os.listdir(self.rootdir)
        filenames.sort()
        for filename in filenames:
            path = os.path.join(self.rootdir, filename)
            if os.path.isdir(path) and filename in test_dirs:
                suite.addTest(
                        self.handle_directory(path, filename))
        return suite

    def handle_directory(self, path, context):
        workdir = os.path.join(self.workdir, context)
        if not os.path.exists(workdir):
            os.makedirs(workdir)

        suite = unittest.TestSuite()
        filenames = os.listdir(path)
        filenames.sort()
        for filename in filenames:
            if os.path.splitext(filename)[1].lower() not in (".f", ".f77", ".f90", ".f95"):
                continue
            if filename.startswith('.'): continue # certain emacs backup files
            basename = os.path.splitext(filename)[0]
            fqbasename = "%s.%s" % (context, basename)
            if not [1 for match in self.selectors if match(fqbasename)]:
                continue
            if self.exclude_selectors:
                if [1 for match in self.exclude_selectors if match(fqbasename)]:
                    continue
            if context in TEST_RUN_DIRS:
                # test_class = FwrapCompileTestCase
                test_class = FwrapRunTestCase
            else:
                test_class = FwrapCompileTestCase
            suite.addTest(self.build_test(test_class, path, workdir, filename))
        return suite

    def build_test(self, test_class, path, workdir, filename):
        return test_class(path, workdir, filename,
                cleanup_workdir=self.cleanup_workdir,
                cleanup_sharedlibs=self.cleanup_sharedlibs)

class _devnull(object):

    def flush(self): pass
    def write(self, s): pass

    def read(self): return ''


class FwrapCompileTestCase(unittest.TestCase):
    def __init__(self, directory, workdir, filename,
            cleanup_workdir=True, cleanup_sharedlibs=True):
        self.directory = directory
        self.workdir = workdir
        self.filename = filename
        self.cleanup_workdir = cleanup_workdir
        self.cleanup_sharedlibs = cleanup_sharedlibs
        unittest.TestCase.__init__(self)

    def shortDescription(self):
        return "wrapping %s" % self.filename

    def setUp(self):
        if self.workdir not in sys.path:
            sys.path.insert(0, self.workdir)

    def tearDown(self):
        try:
            sys.path.remove(self.workdir)
        except ValueError:
            pass
        if os.path.exists(self.workdir):
            if self.cleanup_workdir:
                for rmfile in os.listdir(self.workdir):
                    # if not self.cleanup_workdir:
                        # if rmfile.lower().startswith("wrap") or rmfile.lower().startswith("autoconfig"):
                            # continue
                    # if not self.cleanup_sharedlibs and rmfile.endswith(".so") or rmfile.endswith(".dll"):
                        # continue
                    try:
                        rmfile = os.path.join(self.workdir, rmfile)
                        if os.path.isdir(rmfile):
                            shutil.rmtree(rmfile, ignore_errors=True)
                        else:
                            os.remove(rmfile)
                    except IOError:
                        pass
        else:
            os.makedirs(self.workdirs)

    def runTest(self):
        self.projname = os.path.splitext(self.filename)[0] + '_fwrap'
        self.projdir = os.path.join(self.workdir, self.projname)
        self.wrapped_filename = self.projname+'_fortran.f95'
        self.fwrap_config_source='genconfig.f95'
        self.fwrap_config_module_source='config.f95'
        self.fwrap_cython_source=self.projname+'.pyx'
        fq_fname = os.path.join(os.path.abspath(self.directory), self.filename)
        wrap([fq_fname], self.directory, self.workdir, self.projname)
        # self.runCompileTest()
        self.runCompileTest_distutils()

    def runCompileTest(self):
        from subprocess import Popen, PIPE, STDOUT, CalledProcessError
        p = Popen(['make'], cwd=self.projdir, close_fds=True,
                stdout=PIPE, stderr=STDOUT)
        output = p.communicate()[0]
        if p.returncode:
            raise CalledProcessError(p.returncode, "make")


    def runCompileTest_distutils(self):
        import sys
        CYTHON_DIR = os.path.abspath(os.path.curdir)
        FILENAME = self.filename
        WRAPPED_FILENAME = self.wrapped_filename
        PROJNAME = self.projname
        FWRAP_CONFIG_SOURCE = self.fwrap_config_source
        FWRAP_CONFIG_MODULE_SOURCE = self.fwrap_config_module_source
        FWRAP_CYTHON_SOURCE = self.fwrap_cython_source

        setup_source = '''
import os,sys
sys.path.insert(0, '%(CYTHON_DIR)s')

from fwrap_setup import FwrapExtension, fwrap_build_ext, setup
sources = ['%(FILENAME)s', '%(WRAPPED_FILENAME)s']
ext = FwrapExtension(
            '%(PROJNAME)s',
            sources= ['%(FILENAME)s', '%(WRAPPED_FILENAME)s'],
            fwrap_config_sources=[('%(FWRAP_CONFIG_SOURCE)s', '%(FWRAP_CONFIG_MODULE_SOURCE)s')],
            fwrap_cython_sources=['%(FWRAP_CYTHON_SOURCE)s'],
            )
setup(cmdclass={'build_ext' : fwrap_build_ext},
        ext_modules = [ext])
''' % locals()

        setup_fqpath = os.path.join(self.projdir, 'setup.py')
        f = open(setup_fqpath,'w')
        f.write(setup_source)
        f.close()

        shutil.copy('fwrap_setup.py', self.projdir)
        shutil.copy(os.path.join(self.directory, self.filename), self.projdir)
        from distutils.core import run_setup
        thisdir = os.path.abspath(os.curdir)
        try:
            os.chdir(self.projdir)
            orig_stdout, orig_stderr = sys.stdout, sys.stderr
            sys.stdout = _devnull()
            sys.stderr = _devnull()
            run_setup(setup_fqpath, script_args=['build_ext', '--fcompiler=gnu95', '-lgfortran', '--inplace'])
        finally:
            os.chdir(thisdir)
            sys.stdout,sys.stderr = orig_stdout, orig_stderr

    def build_target_filenames(self, filename):
        fortran_wrapper = "wrap_%s" % filename
        c_header = "wrap_%s.h" % os.path.splitext(filename)[0]
        return (fortran_wrapper, c_header)

    def compile(self, directory, filename, workdir, incdir):
        self.run_wrapper(directory, filename, workdir, incdir)

    def run_wrapper(self, directory, filename, workdir, incdir):
        wrap(filename, directory, workdir)


class FwrapRunTestCase(FwrapCompileTestCase):
    def shortDescription(self):
        return "compiling and running %s" % self.filename

    def run(self, result=None):
        if result is None:
            result = self.defaultTestResult()
        result.startTest(self)
        try:
            self.setUp()
            self.runTest()
            if self.projdir not in sys.path:
                sys.path.insert(0, self.projdir)
            doctest_mod_base = self.projname+'_doctest'
            doctest_mod_fqpath = os.path.join(self.directory, doctest_mod_base+'.py')
            shutil.copy(doctest_mod_fqpath, self.projdir)
            doctest.DocTestSuite(self.projname+'_doctest').run(result) #??
        except Exception:
            result.addError(self, sys.exc_info())
            result.stopTest(self)
        try:
            self.tearDown()
        except Exception:
            pass


class TestBuilder(object):
    def __init__(self, rootdir, workdir, selectors, exclude_selectors, annotate,
                 cleanup_workdir, cleanup_sharedlibs, with_pyregr, cython_only,
                 languages, test_bugs):
        self.rootdir = rootdir
        self.workdir = workdir
        self.selectors = selectors
        self.exclude_selectors = exclude_selectors
        self.annotate = annotate
        self.cleanup_workdir = cleanup_workdir
        self.cleanup_sharedlibs = cleanup_sharedlibs
        self.with_pyregr = with_pyregr
        self.cython_only = cython_only
        self.languages = languages
        self.test_bugs = test_bugs

    def build_suite(self):
        suite = unittest.TestSuite()
        test_dirs = TEST_DIRS
        filenames = os.listdir(self.rootdir)
        filenames.sort()
        for filename in filenames:
            if not WITH_CYTHON and filename == "errors":
                # we won't get any errors without running Cython
                continue
            path = os.path.join(self.rootdir, filename)
            if os.path.isdir(path) and filename in test_dirs:
                if filename == 'pyregr' and not self.with_pyregr:
                    continue
                suite.addTest(
                    self.handle_directory(path, filename))
        return suite

    def handle_directory(self, path, context):
        workdir = os.path.join(self.workdir, context)
        if not os.path.exists(workdir):
            os.makedirs(workdir)

        expect_errors = (context == 'errors')
        suite = unittest.TestSuite()
        filenames = os.listdir(path)
        filenames.sort()
        for filename in filenames:
            if not (filename.endswith(".pyx") or filename.endswith(".py")):
                continue
            if filename.startswith('.'): continue # certain emacs backup files
            if context == 'pyregr' and not filename.startswith('test_'):
                continue
            module = os.path.splitext(filename)[0]
            fqmodule = "%s.%s" % (context, module)
            if not [ 1 for match in self.selectors
                     if match(fqmodule) ]:
                continue
            if self.exclude_selectors:
                if [1 for match in self.exclude_selectors if match(fqmodule)]:
                    continue
            if context in TEST_RUN_DIRS:
                if module.startswith("test_"):
                    test_class = CythonUnitTestCase
                else:
                    test_class = CythonRunTestCase
            else:
                test_class = CythonCompileTestCase
            for test in self.build_tests(test_class, path, workdir,
                                         module, expect_errors):
                suite.addTest(test)
        return suite

    def build_tests(self, test_class, path, workdir, module, expect_errors):
        if expect_errors:
            languages = self.languages[:1]
        else:
            languages = self.languages
        if 'cpp' in module and 'c' in languages:
            languages = list(languages)
            languages.remove('c')
        tests = [ self.build_test(test_class, path, workdir, module,
                                  language, expect_errors)
                  for language in languages ]
        return tests

    def build_test(self, test_class, path, workdir, module,
                   language, expect_errors):
        workdir = os.path.join(workdir, language)
        if not os.path.exists(workdir):
            os.makedirs(workdir)
        return test_class(path, workdir, module,
                          language=language,
                          expect_errors=expect_errors,
                          annotate=self.annotate,
                          cleanup_workdir=self.cleanup_workdir,
                          cleanup_sharedlibs=self.cleanup_sharedlibs,
                          cython_only=self.cython_only)

# class CythonCompileTestCase(unittest.TestCase):
    # def __init__(self, directory, workdir, module, language='c',
                 # expect_errors=False, annotate=False, cleanup_workdir=True,
                 # cleanup_sharedlibs=True, cython_only=False):
        # self.directory = directory
        # self.workdir = workdir
        # self.module = module
        # self.language = language
        # self.expect_errors = expect_errors
        # self.annotate = annotate
        # self.cleanup_workdir = cleanup_workdir
        # self.cleanup_sharedlibs = cleanup_sharedlibs
        # self.cython_only = cython_only
        # unittest.TestCase.__init__(self)

    # def shortDescription(self):
        # return "compiling (%s) %s" % (self.language, self.module)

    # def setUp(self):
        # if self.workdir not in sys.path:
            # sys.path.insert(0, self.workdir)

    # def tearDown(self):
        # try:
            # sys.path.remove(self.workdir)
        # except ValueError:
            # pass
        # try:
            # del sys.modules[self.module]
        # except KeyError:
            # pass
        # cleanup_c_files = WITH_CYTHON and self.cleanup_workdir
        # cleanup_lib_files = self.cleanup_sharedlibs
        # if os.path.exists(self.workdir):
            # for rmfile in os.listdir(self.workdir):
                # if not cleanup_c_files:
                    # if rmfile[-2:] in (".c", ".h") or rmfile[-4:] == ".cpp":
                        # continue
                # if not cleanup_lib_files and rmfile.endswith(".so") or rmfile.endswith(".dll"):
                    # continue
                # if self.annotate and rmfile.endswith(".html"):
                    # continue
                # try:
                    # rmfile = os.path.join(self.workdir, rmfile)
                    # if os.path.isdir(rmfile):
                        # shutil.rmtree(rmfile, ignore_errors=True)
                    # else:
                        # os.remove(rmfile)
                # except IOError:
                    # pass
        # else:
            # os.makedirs(self.workdir)

    # def runTest(self):
        # self.runCompileTest()

    # def runCompileTest(self):
        # self.compile(self.directory, self.module, self.workdir,
                     # self.directory, self.expect_errors, self.annotate)

    # def find_module_source_file(self, source_file):
        # if not os.path.exists(source_file):
            # source_file = source_file[:-1]
        # return source_file

    # def build_target_filename(self, module_name):
        # target = '%s.%s' % (module_name, self.language)
        # return target

    # def split_source_and_output(self, directory, module, workdir):
        # source_file = os.path.join(directory, module) + '.pyx'
        # source_and_output = open(
            # self.find_module_source_file(source_file), 'rU')
        # out = open(os.path.join(workdir, module + '.pyx'), 'w')
        # for line in source_and_output:
            # last_line = line
            # if line.startswith("_ERRORS"):
                # out.close()
                # out = ErrorWriter()
            # else:
                # out.write(line)
        # try:
            # geterrors = out.geterrors
        # except AttributeError:
            # return []
        # else:
            # return geterrors()

    # def run_cython(self, directory, module, targetdir, incdir, annotate):
        # include_dirs = INCLUDE_DIRS[:]
        # if incdir:
            # include_dirs.append(incdir)
        # source = self.find_module_source_file(
            # os.path.join(directory, module + '.pyx'))
        # target = os.path.join(targetdir, self.build_target_filename(module))
        # options = CompilationOptions(
            # pyrex_default_options,
            # include_path = include_dirs,
            # output_file = target,
            # annotate = annotate,
            # use_listing_file = False,
            # cplus = self.language == 'cpp',
            # generate_pxi = False)
        # cython_compile(source, options=options,
                       # full_module_name=module)

    # def run_distutils(self, module, workdir, incdir):
        # cwd = os.getcwd()
        # os.chdir(workdir)
        # try:
            # build_extension = build_ext(distutils_distro)
            # build_extension.include_dirs = INCLUDE_DIRS[:]
            # if incdir:
                # build_extension.include_dirs.append(incdir)
            # build_extension.finalize_options()
            # ext_include_dirs = []
            # for match, get_additional_include_dirs in EXT_DEP_INCLUDES:
                # if match(module):
                    # ext_include_dirs += get_additional_include_dirs()
            # extension = Extension(
                # module,
                # sources = [self.build_target_filename(module)],
                # include_dirs = ext_include_dirs,
                # extra_compile_args = CFLAGS,
                # )
            # if self.language == 'cpp':
                # extension.language = 'c++'
            # build_extension.extensions = [extension]
            # build_extension.build_temp = workdir
            # build_extension.build_lib  = workdir
            # build_extension.run()
        # finally:
            # os.chdir(cwd)

    # def compile(self, directory, module, workdir, incdir,
                # expect_errors, annotate):
        # expected_errors = errors = ()
        # if expect_errors:
            # expected_errors = self.split_source_and_output(
                # directory, module, workdir)
            # directory = workdir

        # if WITH_CYTHON:
            # old_stderr = sys.stderr
            # try:
                # sys.stderr = ErrorWriter()
                # self.run_cython(directory, module, workdir, incdir, annotate)
                # errors = sys.stderr.geterrors()
            # finally:
                # sys.stderr = old_stderr

        # if errors or expected_errors:
            # for expected, error in zip(expected_errors, errors):
                # self.assertEquals(expected, error)
            # if len(errors) < len(expected_errors):
                # expected_error = expected_errors[len(errors)]
                # self.assertEquals(expected_error, None)
            # elif len(errors) > len(expected_errors):
                # unexpected_error = errors[len(expected_errors)]
                # self.assertEquals(None, unexpected_error)
        # else:
            # if not self.cython_only:
                # self.run_distutils(module, workdir, incdir)

# class CythonRunTestCase(CythonCompileTestCase):
    # def shortDescription(self):
        # return "compiling (%s) and running %s" % (self.language, self.module)

    # def run(self, result=None):
        # if result is None:
            # result = self.defaultTestResult()
        # result.startTest(self)
        # try:
            # self.setUp()
            # self.runCompileTest()
            # if not self.cython_only:
                # doctest.DocTestSuite(self.module).run(result)
        # except Exception:
            # result.addError(self, sys.exc_info())
            # result.stopTest(self)
        # try:
            # self.tearDown()
        # except Exception:
            # pass

# class CythonUnitTestCase(CythonCompileTestCase):
    # def shortDescription(self):
        # return "compiling (%s) tests in %s" % (self.language, self.module)

    # def run(self, result=None):
        # if result is None:
            # result = self.defaultTestResult()
        # result.startTest(self)
        # try:
            # self.setUp()
            # self.runCompileTest()
            # unittest.defaultTestLoader.loadTestsFromName(self.module).run(result)
        # except Exception:
            # result.addError(self, sys.exc_info())
            # result.stopTest(self)
        # try:
            # self.tearDown()
        # except Exception:
            # pass

def collect_unittests(path, module_prefix, suite, selectors):
    def file_matches(filename):
        return filename.startswith("Test") and filename.endswith(".py")

    def package_matches(dirname):
        return dirname == "Tests"

    loader = unittest.TestLoader()

    skipped_dirs = []

    for dirpath, dirnames, filenames in os.walk(path):
        if dirpath != path and "__init__.py" not in filenames:
            skipped_dirs.append(dirpath + os.path.sep)
            continue
        skip = False
        for dir in skipped_dirs:
            if dirpath.startswith(dir):
                skip = True
        if skip:
            continue
        parentname = os.path.split(dirpath)[-1]
        if package_matches(parentname):
            for f in filenames:
                if file_matches(f):
                    filepath = os.path.join(dirpath, f)[:-len(".py")]
                    modulename = module_prefix + filepath[len(path)+1:].replace(os.path.sep, '.')
                    if not [ 1 for match in selectors if match(modulename) ]:
                        continue
                    module = __import__(modulename)
                    for x in modulename.split('.')[1:]:
                        module = getattr(module, x)
                    suite.addTests([loader.loadTestsFromModule(module)])

def collect_doctests(path, module_prefix, suite, selectors):
    def package_matches(dirname):
        return dirname not in ("Mac", "Distutils", "Plex")
    def file_matches(filename):
        return (filename.endswith(".py") and not ('~' in filename
                or '#' in filename or filename.startswith('.')))
    import doctest, types
    for dirpath, dirnames, filenames in os.walk(path):
        parentname = os.path.split(dirpath)[-1]
        if package_matches(parentname):
            for f in filenames:
                if file_matches(f):
                    if not f.endswith('.py'): continue
                    filepath = os.path.join(dirpath, f)[:-len(".py")]
                    modulename = module_prefix + filepath[len(path)+1:].replace(os.path.sep, '.')
                    if not [ 1 for match in selectors if match(modulename) ]:
                        continue
                    module = __import__(modulename)
                    for x in modulename.split('.')[1:]:
                        module = getattr(module, x)
                    if hasattr(module, "__doc__") or hasattr(module, "__test__"):
                        try:
                            suite.addTest(doctest.DocTestSuite(module))
                        except ValueError: # no tests
                            pass

class MissingDependencyExcluder:
    def __init__(self, deps):
        # deps: { module name : matcher func }
        self.exclude_matchers = []
        for mod, matcher in deps.items():
            try:
                __import__(mod)
            except ImportError:
                self.exclude_matchers.append(matcher)
        self.tests_missing_deps = []
    def __call__(self, testname):
        for matcher in self.exclude_matchers:
            if matcher(testname):
                self.tests_missing_deps.append(testname)
                return True
        return False

class VersionDependencyExcluder:
    def __init__(self, deps):
        # deps: { version : matcher func }
        from sys import version_info
        self.exclude_matchers = []
        for ver, matcher in deps.items():
            if version_info < ver:
                self.exclude_matchers.append(matcher)
        self.tests_missing_deps = []
    def __call__(self, testname):
        for matcher in self.exclude_matchers:
            if matcher(testname):
                self.tests_missing_deps.append(testname)
                return True
        return False

class FileListExcluder:

    def __init__(self, list_file):
        self.excludes = {}
        for line in open(list_file).readlines():
            line = line.strip()
            if line and line[0] != '#':
                self.excludes[line.split()[0]] = True
                
    def __call__(self, testname):
        return testname.split('.')[-1] in self.excludes

if __name__ == '__main__':
    from optparse import OptionParser
    parser = OptionParser()
    parser.add_option("--no-cleanup", dest="cleanup_workdir",
                      action="store_false", default=True,
                      help="do not delete the generated C files (allows passing --no-cython on next run)")
    parser.add_option("--no-cleanup-sharedlibs", dest="cleanup_sharedlibs",
                      action="store_false", default=True,
                      help="do not delete the generated shared libary files (allows manual module experimentation)")
    # parser.add_option("--no-cython", dest="with_cython",
                      # action="store_false", default=True,
                      # help="do not run the Cython compiler, only the C compiler")
    # parser.add_option("--no-c", dest="use_c",
                      # action="store_false", default=True,
                      # help="do not test C compilation")
    # parser.add_option("--no-cpp", dest="use_cpp",
                      # action="store_false", default=True,
                      # help="do not test C++ compilation")
    # parser.add_option("--no-unit", dest="unittests",
                      # action="store_false", default=True,
                      # help="do not run the unit tests")
    # parser.add_option("--no-doctest", dest="doctests",
                      # action="store_false", default=True,
                      # help="do not run the doctests")
    # parser.add_option("--no-file", dest="filetests",
                      # action="store_false", default=True,
                      # help="do not run the file based tests")
    # parser.add_option("--no-pyregr", dest="pyregr",
                      # action="store_false", default=True,
                      # help="do not run the regression tests of CPython in tests/pyregr/")    
    # parser.add_option("--cython-only", dest="cython_only",
                      # action="store_true", default=False,
                      # help="only compile pyx to c, do not run C compiler or run the tests")
    # parser.add_option("--no-refnanny", dest="with_refnanny",
                      # action="store_false", default=True,
                      # help="do not regression test reference counting")
    # parser.add_option("--sys-pyregr", dest="system_pyregr",
                      # action="store_true", default=False,
                      # help="run the regression tests of the CPython installation")
    parser.add_option("-x", "--exclude", dest="exclude",
                      action="append", metavar="PATTERN",
                      help="exclude tests matching the PATTERN")
    # parser.add_option("-C", "--coverage", dest="coverage",
                      # action="store_true", default=False,
                      # help="collect source coverage data for the Compiler")
    # parser.add_option("-A", "--annotate", dest="annotate_source",
                      # action="store_true", default=True,
                      # help="generate annotated HTML versions of the test source files")
    # parser.add_option("--no-annotate", dest="annotate_source",
                      # action="store_false",
                      # help="do not generate annotated HTML versions of the test source files")
    parser.add_option("-v", "--verbose", dest="verbosity",
                      action="count", default=0,
                      help="display test progress, pass twice to print test names")
    parser.add_option("-T", "--ticket", dest="tickets",
                      action="append",
                      help="a bug ticket number to run the respective test in 'tests/bugs'")

    options, cmd_args = parser.parse_args()

    if 0:
        if sys.version_info[0] >= 3:
            # make sure we do not import (or run) Cython itself
            options.doctests    = False
            options.with_cython = False
            options.unittests   = False
            options.pyregr      = False

        if options.coverage:
            import coverage
            coverage.erase()
            coverage.start()

        WITH_CYTHON = options.with_cython

        if WITH_CYTHON:
            from Cython.Compiler.Main import \
                CompilationOptions, \
                default_options as pyrex_default_options, \
                compile as cython_compile
            from Cython.Compiler import Errors
            Errors.LEVEL = 0 # show all warnings
    # if 0

    # RUN ALL TESTS!
    ROOTDIR = os.path.join(os.getcwd(), os.path.dirname(sys.argv[0]), 'tests')
    WORKDIR = os.path.join(os.getcwd(), 'BUILD')
    # UNITTEST_MODULE = "Cython"
    # UNITTEST_ROOT = os.path.join(os.getcwd(), UNITTEST_MODULE)
    if os.path.exists(WORKDIR):
        for path in os.listdir(WORKDIR):
            if path in ("support",): continue
            shutil.rmtree(os.path.join(WORKDIR, path), ignore_errors=True)
    if not os.path.exists(WORKDIR):
        os.makedirs(WORKDIR)

    if 0:
        if WITH_CYTHON:
            from Cython.Compiler.Version import version
            sys.stderr.write("Running tests against Cython %s\n" % version)
            from Cython.Compiler import DebugFlags
            DebugFlags.debug_temp_code_comments = 1
        else:
            sys.stderr.write("Running tests without Cython.\n")
    #if 0

    from fwrap_src.Main import wrap

    sys.stderr.write("Python %s\n" % sys.version)
    sys.stderr.write("\n")

    # insert cython.py/Cython source directory into sys.path
    cython_dir = os.path.abspath(os.path.join(os.path.pardir, os.path.pardir))
    sys.path.insert(0, cython_dir)

    if 0:
        if options.with_refnanny:
            from pyximport.pyxbuild import pyx_to_dll
            libpath = pyx_to_dll(os.path.join("Cython", "Runtime", "refnanny.pyx"),
                                 build_in_temp=True,
                                 pyxbuild_dir=os.path.join(WORKDIR, "support"))
            sys.path.insert(0, os.path.split(libpath)[0])
            CFLAGS.append("-DCYTHON_REFNANNY")

    #if 0
    test_bugs = False
    if options.tickets:
        for ticket_number in options.tickets:
            test_bugs = True
            cmd_args.append('.*T%s$' % ticket_number)
    if not test_bugs:
        for selector in cmd_args:
            if selector.startswith('bugs'):
                test_bugs = True


    import re
    selectors = [ re.compile(r, re.I|re.U).search for r in cmd_args ]
    if not selectors:
        selectors = [ lambda x:True ]

    # Chech which external modules are not present and exclude tests
    # which depends on them (by prefix)

    exclude_selectors = []

    if options.exclude:
        exclude_selectors += [ re.compile(r, re.I|re.U).search for r in options.exclude ]

    if 0:
        missing_dep_excluder = MissingDependencyExcluder(EXT_DEP_MODULES) 
        version_dep_excluder = VersionDependencyExcluder(VER_DEP_MODULES) 
        exclude_selectors = [missing_dep_excluder, version_dep_excluder] # want to pring msg at exit

        
    if not test_bugs:
        exclude_selectors += [ FileListExcluder("tests/bugs.txt") ]

    if 0:
        languages = []
        if options.use_c:
            languages.append('c')
        if options.use_cpp:
            languages.append('cpp')
    # if 0

    test_suite = unittest.TestSuite()

    if 0:
        if options.unittests:
            collect_unittests(UNITTEST_ROOT, UNITTEST_MODULE + ".", test_suite, selectors)

        if options.doctests:
            collect_doctests(UNITTEST_ROOT, UNITTEST_MODULE + ".", test_suite, selectors)
    # if 0

    filetests = FwrapTestBuilder(ROOTDIR, WORKDIR, selectors, exclude_selectors,
            options.cleanup_workdir, options.cleanup_sharedlibs)
    test_suite.addTest(filetests.build_suite())

    if 0:
        if options.filetests and languages:
            filetests = TestBuilder(ROOTDIR, WORKDIR, selectors, exclude_selectors,
                                    options.annotate_source, options.cleanup_workdir,
                                    options.cleanup_sharedlibs, options.pyregr,
                                    options.cython_only, languages, test_bugs)
            test_suite.addTest(filetests.build_suite())

        if options.system_pyregr and languages:
            filetests = TestBuilder(ROOTDIR, WORKDIR, selectors, exclude_selectors,
                                    options.annotate_source, options.cleanup_workdir,
                                    options.cleanup_sharedlibs, True,
                                    options.cython_only, languages, test_bugs)
            test_suite.addTest(
                filetests.handle_directory(
                    os.path.join(sys.prefix, 'lib', 'python'+sys.version[:3], 'test'),
                    'pyregr'))

    unittest.TextTestRunner(verbosity=options.verbosity).run(test_suite)

    if 0:
        if options.coverage:
            coverage.stop()
            ignored_modules = ('Options', 'Version', 'DebugFlags', 'CmdLine')
            modules = [ module for name, module in sys.modules.items()
                        if module is not None and
                        name.startswith('Cython.Compiler.') and 
                        name[len('Cython.Compiler.'):] not in ignored_modules ]
            coverage.report(modules, show_missing=0)

        if missing_dep_excluder.tests_missing_deps:
            sys.stderr.write("Following tests excluded because of missing dependencies on your system:\n")
            for test in missing_dep_excluder.tests_missing_deps:
                sys.stderr.write("   %s\n" % test)

        if options.with_refnanny:
            import refnanny
            sys.stderr.write("\n".join([repr(x) for x in refnanny.reflog]))
