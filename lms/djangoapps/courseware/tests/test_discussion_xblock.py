"""
Tests for the discussion xblock.

Most of the tests are in common/xblock/xblock_discussion, here are only
tests for functionalities that require django API, and lms specific
functionalities.
"""


import json
import uuid

from unittest import mock
import ddt
from django.test import override_settings
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag
from opaque_keys.edx.keys import CourseKey
from web_fragments.fragment import Fragment
from xblock.field_data import DictFieldData

from lms.djangoapps.course_api.blocks.tests.helpers import deserialize_usage_key
from lms.djangoapps.courseware.module_render import get_module_for_descriptor_internal
from lms.djangoapps.courseware.tests.helpers import XModuleRenderingTestBase
from lms.djangoapps.discussion.toggles import ENABLE_DISCUSSIONS_MFE
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from xblock_discussion import DiscussionXBlock, loader

from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import ItemFactory, ToyCourseFactory


@ddt.ddt
class TestDiscussionXBlock(XModuleRenderingTestBase):
    """
    Base class for tests
    """

    PATCH_DJANGO_USER = True

    def setUp(self):
        """
        Set up the xblock runtime, test course, discussion, and user.
        """
        super().setUp()
        self.patchers = []
        self.course_id = CourseKey.from_string("course-v1:test+test+test_course")
        self.runtime = self.new_module_runtime()
        self.runtime.modulestore = mock.Mock()

        self.discussion_id = str(uuid.uuid4())
        self.data = DictFieldData({
            'discussion_id': self.discussion_id
        })
        scope_ids = mock.Mock()
        scope_ids.usage_id.course_key = self.course_id
        self.block = DiscussionXBlock(
            self.runtime,
            field_data=self.data,
            scope_ids=scope_ids
        )
        self.block.xmodule_runtime = mock.Mock()

        if self.PATCH_DJANGO_USER:
            self.django_user_canary = UserFactory()
            self.django_user_mock = self.add_patcher(
                mock.patch.object(DiscussionXBlock, "django_user", new_callable=mock.PropertyMock)
            )
            self.django_user_mock.return_value = self.django_user_canary

    def add_patcher(self, patcher):
        """
        Registers a patcher object, and returns mock. This patcher will be disabled after the test.
        """
        self.patchers.append(patcher)
        return patcher.start()

    def tearDown(self):
        """
        Tears down any patchers added during tests.
        """
        super().tearDown()
        for patcher in self.patchers:
            patcher.stop()


class TestGetDjangoUser(TestDiscussionXBlock):
    """
    Tests for the django_user property.
    """

    PATCH_DJANGO_USER = False

    def setUp(self):
        """
        Mock the user service and runtime.
        """
        super().setUp()
        self.django_user = object()
        self.user_service = mock.Mock()
        self.add_patcher(
            mock.patch.object(self.runtime, "service", return_value=self.user_service)
        )
        self.user_service._django_user = self.django_user  # pylint: disable=protected-access

    def test_django_user(self):
        """
        Tests that django_user users returns _django_user attribute
        of the user service.
        """
        actual_user = self.block.django_user
        self.runtime.service.assert_called_once_with(  # lint-amnesty, pylint: disable=no-member
            self.block, 'user')
        assert actual_user == self.django_user

    def test_django_user_handles_missing_service(self):
        """
        Tests that get_django gracefully handles missing user service.
        """
        self.runtime.service.return_value = None
        assert self.block.django_user is None


@ddt.ddt
class TestViews(TestDiscussionXBlock):
    """
    Tests for student_view and author_view.
    """

    def setUp(self):
        """
        Mock the methods needed for these tests.
        """
        super().setUp()
        self.template_canary = 'canary'
        self.render_template = mock.Mock()
        self.render_template.return_value = self.template_canary
        self.block.runtime.render_template = self.render_template
        self.has_permission_mock = mock.Mock()
        self.has_permission_mock.return_value = False
        self.block.has_permission = self.has_permission_mock

    def get_template_context(self):
        """
        Returns context passed to rendering of the django template
        (rendered by runtime).
        """
        assert self.render_template.call_count == 1
        return self.render_template.call_args_list[0][0][1]

    def get_rendered_template(self):
        """
        Returns the name of the template rendered by runtime.
        """
        assert self.render_template.call_count == 1
        return self.render_template.call_args_list[0][0][0]

    def test_studio_view(self):
        """
        Test for the studio view.
        """
        fragment = self.block.author_view()
        assert isinstance(fragment, Fragment)
        assert fragment.content == self.template_canary
        self.render_template.assert_called_once_with(
            'discussion/_discussion_inline_studio.html',
            {'discussion_id': self.discussion_id}
        )

    @ddt.data(
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (False, False, True),
    )
    def test_student_perms_are_correct(self, permissions):
        """
        Test that context will get proper permissions.
        """
        permission_dict = {
            'create_thread': permissions[0],
            'create_comment': permissions[1],
            'create_sub_comment': permissions[2]
        }

        expected_permissions = {
            'can_create_thread': permission_dict['create_thread'],
            'can_create_comment': permission_dict['create_comment'],
            'can_create_subcomment': permission_dict['create_sub_comment'],
        }

        self.block.has_permission = lambda perm: permission_dict[perm]
        with mock.patch.object(loader, 'render_template', mock.Mock):
            self.block.student_view()

        context = self.get_template_context()

        for permission_name, expected_value in expected_permissions.items():
            assert expected_value == context[permission_name]

    def test_js_init(self):
        """
        Test proper js init function is called.
        """
        with mock.patch.object(loader, 'render_template', mock.Mock):
            fragment = self.block.student_view()
        assert fragment.js_init_fn == 'DiscussionInlineBlock'


@ddt.ddt
class TestTemplates(TestDiscussionXBlock):
    """
    Tests rendering of templates.
    """

    def test_has_permission(self):
        """
        Test for has_permission method.
        """
        permission_canary = object()
        with mock.patch(
            'lms.djangoapps.discussion.django_comment_client.permissions.has_permission',
            return_value=permission_canary,
        ) as has_perm:
            actual_permission = self.block.has_permission("test_permission")
        assert actual_permission == permission_canary
        has_perm.assert_called_once_with(self.django_user_canary, 'test_permission', self.course_id)

    def test_studio_view(self):
        """Test for studio view."""
        fragment = self.block.author_view({})
        assert f'data-discussion-id="{self.discussion_id}"' in fragment.content

    @ddt.data(
        (True, False, False),
        (False, True, False),
        (False, False, True),
    )
    def test_student_perms_are_correct(self, permissions):
        """
        Test for lms view.
        """
        permission_dict = {
            'create_thread': permissions[0],
            'create_comment': permissions[1],
            'create_sub_comment': permissions[2]
        }

        self.block.has_permission = lambda perm: permission_dict[perm]
        fragment = self.block.student_view()
        read_only = 'false' if permissions[0] else 'true'
        assert f'data-discussion-id="{self.discussion_id}"' in fragment.content
        assert f'data-user-create-comment="{json.dumps(permissions[1])}"' in fragment.content
        assert f'data-user-create-subcomment="{json.dumps(permissions[2])}"' in fragment.content
        assert f'data-read-only="{read_only}"' in fragment.content


@ddt.ddt
class TestXBlockInCourse(SharedModuleStoreTestCase):
    """
    Test the discussion xblock as rendered in the course and course API.
    """

    @classmethod
    def setUpClass(cls):
        """
        Set up a user, course, and discussion XBlock for use by tests.
        """
        super().setUpClass()
        cls.user = UserFactory()
        cls.course = ToyCourseFactory.create()
        cls.course_key = cls.course.id
        cls.course_usage_key = cls.store.make_course_usage_key(cls.course_key)
        cls.discussion_id = "test_discussion_xblock_id"
        cls.discussion = ItemFactory.create(
            parent_location=cls.course_usage_key,
            category='discussion',
            discussion_id=cls.discussion_id,
            discussion_category='Category discussion',
            discussion_target='Target Discussion',
        )
        CourseEnrollmentFactory.create(user=cls.user, course_id=cls.course_key)

    def get_root(self, block):
        """
        Return root of the block.
        """
        while block.parent:
            block = block.get_parent()
        return block

    def test_html_with_user(self):
        """
        Test rendered DiscussionXBlock permissions.
        """
        discussion_xblock = get_module_for_descriptor_internal(
            user=self.user,
            descriptor=self.discussion,
            student_data=mock.Mock(name='student_data'),
            course_id=self.course.id,
            track_function=mock.Mock(name='track_function'),
            xqueue_callback_url_prefix=mock.Mock(name='xqueue_callback_url_prefix'),
            request_token='request_token',
        )

        fragment = discussion_xblock.render('student_view')
        html = fragment.content
        assert 'data-user-create-comment="false"' in html
        assert 'data-user-create-subcomment="false"' in html

    @override_settings(DISCUSSIONS_MICROFRONTEND_URL="http://test.url")
    @override_waffle_flag(ENABLE_DISCUSSIONS_MFE, True)
    def test_embed_mfe_in_course(self):
        """
        Test that the xblock embeds the MFE UI when the flag is enabled
        """
        discussion_xblock = get_module_for_descriptor_internal(
            user=self.user,
            descriptor=self.discussion,
            student_data=mock.Mock(name='student_data'),
            course_id=self.course.id,
            track_function=mock.Mock(name='track_function'),
            xqueue_callback_url_prefix=mock.Mock(name='xqueue_callback_url_prefix'),
            request_token='request_token',
        )

        fragment = discussion_xblock.render('student_view')
        html = fragment.content
        self.assertInHTML(
            """
            <iframe
                id='discussions-mfe-tab-embed'
                title='Discussions'
                src='http://test.url/discussions/edX/toy/2012_Fall/topics/test_discussion_xblock_id'
            />
            """,
            html,
        )

    @ddt.data(ModuleStoreEnum.Type.mongo, ModuleStoreEnum.Type.split)
    def test_discussion_render_successfully_with_orphan_parent(self, default_store):
        """
        Test that discussion xblock render successfully
        if discussion xblock is child of an orphan.
        """
        with self.store.default_store(default_store):
            orphan_sequential = self.store.create_item(self.user.id, self.course.id, 'sequential')

            vertical = self.store.create_child(
                self.user.id,
                orphan_sequential.location,
                'vertical',
                block_id=self.course.location.block_id
            )

            discussion = self.store.create_child(
                self.user.id,
                vertical.location,
                'discussion',
                block_id=self.course.location.block_id
            )

            discussion = self.store.get_item(discussion.location)

            root = self.get_root(discussion)
            # Assert that orphan sequential is root of the discussion xblock.
            assert orphan_sequential.location.block_type == root.location.block_type
            assert orphan_sequential.location.block_id == root.location.block_id

            # Get xblock bound to a user and a descriptor.
            discussion_xblock = get_module_for_descriptor_internal(
                user=self.user,
                descriptor=discussion,
                student_data=mock.Mock(name='student_data'),
                course_id=self.course.id,
                track_function=mock.Mock(name='track_function'),
                xqueue_callback_url_prefix=mock.Mock(name='xqueue_callback_url_prefix'),
                request_token='request_token',
            )

            fragment = discussion_xblock.render('student_view')
            html = fragment.content

            assert isinstance(discussion_xblock, DiscussionXBlock)
            assert 'data-user-create-comment="false"' in html
            assert 'data-user-create-subcomment="false"' in html

    def test_discussion_student_view_data(self):
        """
        Tests that course block api returns student_view_data for discussion xblock
        """
        self.client.login(username=self.user.username, password='test')
        url = reverse('blocks_in_block_tree', kwargs={'usage_key_string': str(self.course_usage_key)})
        query_params = {
            'depth': 'all',
            'username': self.user.username,
            'block_types_filter': 'discussion',
            'student_view_data': 'discussion'
        }
        response = self.client.get(url, query_params)
        assert response.status_code == 200
        assert response.data['root'] == str(self.course_usage_key)
        for block_key_string, block_data in response.data['blocks'].items():
            block_key = deserialize_usage_key(block_key_string, self.course_key)
            assert block_data['id'] == block_key_string
            assert block_data['type'] == block_key.block_type
            assert block_data['display_name'] == (self.store.get_item(block_key).display_name or '')
            assert block_data['student_view_data'] == {'topic_id': self.discussion_id}


class TestXBlockQueryLoad(SharedModuleStoreTestCase):
    """
    Test the number of queries executed when rendering the XBlock.
    """

    def test_permissions_query_load(self):
        """
        Tests that the permissions queries are cached when rendering numerous discussion XBlocks.
        """
        user = UserFactory()
        course = ToyCourseFactory()
        course_key = course.id
        course_usage_key = self.store.make_course_usage_key(course_key)
        discussions = []

        for counter in range(5):
            discussion_id = f'test_discussion_{counter}'
            discussions.append(ItemFactory.create(
                parent_location=course_usage_key,
                category='discussion',
                discussion_id=discussion_id,
                discussion_category='Category discussion',
                discussion_target='Target Discussion',
            ))

        # 4 queries are required to do first discussion xblock render:
        # * waffle_utils_wafflecourseoverridemodel
        # * waffle_flag
        # * django_comment_client_role
        # * lms_xblock_xblockasidesconfig
        num_queries = 4
        for discussion in discussions:
            discussion_xblock = get_module_for_descriptor_internal(
                user=user,
                descriptor=discussion,
                student_data=mock.Mock(name='student_data'),
                course_id=course.id,
                track_function=mock.Mock(name='track_function'),
                xqueue_callback_url_prefix=mock.Mock(name='xqueue_callback_url_prefix'),
                request_token='request_token',
            )
            with self.assertNumQueries(num_queries):
                fragment = discussion_xblock.render('student_view')

            # Permissions are cached, so only 1  query required for subsequent renders
            # to check the waffle flag
            num_queries = 1

            html = fragment.content
            assert 'data-user-create-comment="false"' in html
            assert 'data-user-create-subcomment="false"' in html
